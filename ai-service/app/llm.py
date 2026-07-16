import logging
import time
import asyncio
import httpx
import openai
from typing import Optional, List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)


class ResilientLLMClient:
    """A resilient LLM client that delegates invoke/ainvoke calls.

    It supports fallback chain routing, automatic retries per provider, error classification,
    and metadata enrichment (preserving provider, model, latency, and token usage).
    """

    def __init__(self, primary_provider: str, model_name: Optional[str] = None):
        self.primary_provider = primary_provider
        self.model_name = model_name
        self.fallback_chain = settings.llm_fallback_chain

        # Build sequence of provider configurations to attempt
        self.providers: List[Dict[str, Any]] = []

        # Add primary provider
        primary_model = self._get_default_model(primary_provider, model_name)
        self.providers.append({
            "provider": primary_provider,
            "model": primary_model
        })

        # Add fallback providers from configuration
        for fb in self.fallback_chain:
            fb_provider = fb.get("provider")
            fb_model = fb.get("model")
            if fb_provider and fb_provider != primary_provider:
                self.providers.append({
                    "provider": fb_provider,
                    "model": fb_model
                })

    def _get_default_model(self, provider: str, override_model: Optional[str] = None) -> str:
        if provider == "openrouter":
            return override_model or settings.OPENROUTER_MODEL or "openai/gpt-4o-mini"
        elif provider == "groq":
            return override_model or settings.GROQ_MODEL or "llama-3.3-70b-versatile"
        else:
            return override_model or settings.OPENAI_MODEL or "gpt-4o-mini"

    def _instantiate_client(self, provider: str, model: str):
        from langchain_openai import ChatOpenAI

        if provider == "openrouter":
            extra_headers = {}
            if settings.OPENROUTER_APP_NAME:
                extra_headers["X-OpenRouter-Title"] = settings.OPENROUTER_APP_NAME
            if settings.OPENROUTER_APP_URL:
                extra_headers["HTTP-Referer"] = settings.OPENROUTER_APP_URL

            return ChatOpenAI(
                model=model,
                temperature=0,
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
                default_headers=extra_headers if extra_headers else None,
            )
        elif provider == "groq":
            return ChatOpenAI(
                model=model,
                temperature=0,
                api_key=settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
            )
        else:  # openai
            return ChatOpenAI(
                model=model,
                temperature=0,
                api_key=settings.OPENAI_API_KEY,
            )

    def _is_retryable_error(self, exc: Exception) -> bool:
        # Catch rate limit, connection, timeout, and internal server errors
        if isinstance(exc, (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            httpx.HTTPError,
            asyncio.TimeoutError,
            TimeoutError
        )):
            return True
        # Support name-matching for cases where imports might differ dynamically
        cls_name = exc.__class__.__name__
        if cls_name in (
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
            "InternalServerError",
            "HTTPError",
            "TimeoutError"
        ):
            return True
        return False

    def invoke(self, messages, **kwargs):
        last_error = None
        for config in self.providers:
            provider = config["provider"]
            model = config["model"]
            
            try:
                client = self._instantiate_client(provider, model)
            except Exception as e:
                logger.warning("Failed to instantiate client for %s:%s: %s", provider, model, e)
                last_error = e
                continue

            for attempt in range(1, 4):
                start_time = time.time()
                try:
                    logger.info("Invoking LLM (sync) using %s:%s (attempt %d/3)", provider, model, attempt)
                    response = client.invoke(messages, **kwargs)
                    latency_ms = int((time.time() - start_time) * 1000)

                    self._enrich_response_metadata(response, provider, model, latency_ms)
                    logger.info("LLM Success (sync) via %s:%s in %dms", provider, model, latency_ms)
                    return response
                except Exception as exc:
                    latency_ms = int((time.time() - start_time) * 1000)
                    if not self._is_retryable_error(exc) or attempt == 3:
                        logger.warning("Failed LLM attempt (sync) using %s:%s in %dms: %s", provider, model, latency_ms, exc)
                        last_error = exc
                        if not self._is_retryable_error(exc):
                            raise exc
                        break  # move to next provider
                    logger.warning("Retryable error on %s:%s (attempt %d): %s. Retrying in 1s...", provider, model, attempt, exc)
                    time.sleep(1.0)

        raise RuntimeError(f"All configured LLM providers failed. Last error: {last_error}") from last_error

    async def ainvoke(self, messages, **kwargs):
        last_error = None
        for config in self.providers:
            provider = config["provider"]
            model = config["model"]

            try:
                client = self._instantiate_client(provider, model)
            except Exception as e:
                logger.warning("Failed to instantiate client for %s:%s: %s", provider, model, e)
                last_error = e
                continue

            for attempt in range(1, 4):
                start_time = time.time()
                try:
                    logger.info("Invoking LLM (async) using %s:%s (attempt %d/3)", provider, model, attempt)
                    response = await client.ainvoke(messages, **kwargs)
                    latency_ms = int((time.time() - start_time) * 1000)

                    self._enrich_response_metadata(response, provider, model, latency_ms)
                    logger.info("LLM Success (async) via %s:%s in %dms", provider, model, latency_ms)
                    return response
                except Exception as exc:
                    latency_ms = int((time.time() - start_time) * 1000)
                    if not self._is_retryable_error(exc) or attempt == 3:
                        logger.warning("Failed LLM attempt (async) using %s:%s in %dms: %s", provider, model, latency_ms, exc)
                        last_error = exc
                        if not self._is_retryable_error(exc):
                            raise exc
                        break  # move to next provider
                    logger.warning("Retryable error on %s:%s (attempt %d): %s. Retrying in 1s...", provider, model, attempt, exc)
                    await asyncio.sleep(1.0)

        raise RuntimeError(f"All configured LLM providers failed. Last error: {last_error}") from last_error

    def _enrich_response_metadata(self, response: Any, provider: str, model: str, latency_ms: int):
        if not hasattr(response, "response_metadata") or response.response_metadata is None:
            response.response_metadata = {}

        response.response_metadata["provider"] = provider
        response.response_metadata["model"] = model
        response.response_metadata["latency_ms"] = latency_ms

        token_usage = {}
        if "token_usage" in response.response_metadata:
            token_usage = response.response_metadata["token_usage"]
        elif hasattr(response, "usage_metadata") and response.usage_metadata:
            token_usage = response.usage_metadata

        if token_usage:
            response.response_metadata["token_usage"] = {
                "prompt_tokens": token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0,
                "completion_tokens": token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0,
                "total_tokens": token_usage.get("total_tokens") or 0,
            }


def get_llm(model_name: Optional[str] = None):
    """Return a resilient chat LLM client or direct client.

    Uses `settings.LLM_PROVIDER` as primary and parses `settings.LLM_FALLBACK_CHAIN`
    for any subsequent providers to route to on failure.
    """
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider not in ("openrouter", "openai", "groq"):
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")

    if settings.LLM_FALLBACK_CHAIN:
        return ResilientLLMClient(primary_provider=provider, model_name=model_name)

    from langchain_openai import ChatOpenAI

    if provider == "openrouter":
        model = model_name or settings.OPENROUTER_MODEL or "openai/gpt-4o-mini"
        extra_headers = {}
        if settings.OPENROUTER_APP_NAME:
            extra_headers["X-OpenRouter-Title"] = settings.OPENROUTER_APP_NAME
        if settings.OPENROUTER_APP_URL:
            extra_headers["HTTP-Referer"] = settings.OPENROUTER_APP_URL

        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            default_headers=extra_headers if extra_headers else None,
        )
    elif provider == "groq":
        model = model_name or settings.GROQ_MODEL or "llama-3.3-70b-versatile"
        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
    else:  # openai
        model = model_name or settings.OPENAI_MODEL or "gpt-4o-mini"
        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=settings.OPENAI_API_KEY,
        )
