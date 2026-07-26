import logging
import time
import asyncio
import random
import threading
import weakref
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx
import openai
from typing import Optional, List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)

_gate_lock = threading.Lock()
_sync_gates: Dict[int, threading.BoundedSemaphore] = {}
_async_gates: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[int, asyncio.Semaphore]]" = (
    weakref.WeakKeyDictionary()
)


def _configured_concurrency() -> int:
    return max(1, int(getattr(settings, "LLM_MAX_CONCURRENCY", 2)))


def _sync_gate() -> threading.BoundedSemaphore:
    limit = _configured_concurrency()
    with _gate_lock:
        gate = _sync_gates.get(limit)
        if gate is None:
            gate = threading.BoundedSemaphore(limit)
            _sync_gates[limit] = gate
        return gate


def _async_gate() -> asyncio.Semaphore:
    """Return one shared gate per event loop and configured limit."""
    loop = asyncio.get_running_loop()
    limit = _configured_concurrency()
    with _gate_lock:
        gates_for_loop = _async_gates.setdefault(loop, {})
        gate = gates_for_loop.get(limit)
        if gate is None:
            gate = asyncio.Semaphore(limit)
            gates_for_loop[limit] = gate
        return gate


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None

    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(str(value))
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    base = max(0.0, float(getattr(settings, "LLM_RETRY_BASE_SECONDS", 1.0)))
    maximum = max(base, float(getattr(settings, "LLM_RETRY_MAX_SECONDS", 30.0)))
    retry_after = _retry_after_seconds(exc)
    exponential = base * (2 ** max(0, attempt - 1))
    requested_delay = max(exponential, retry_after or 0.0)
    jitter = random.uniform(0.0, min(1.0, requested_delay * 0.25))
    return min(maximum, requested_delay + jitter)


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
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                max_retries=0,
            )
        elif provider == "groq":
            return ChatOpenAI(
                model=model,
                temperature=0,
                api_key=settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                max_retries=0,
            )
        else:  # openai
            return ChatOpenAI(
                model=model,
                temperature=0,
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                max_retries=0,
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

            max_attempts = 1 + max(0, int(getattr(settings, "LLM_MAX_RETRIES", 2)))
            for attempt in range(1, max_attempts + 1):
                start_time = time.time()
                try:
                    logger.info(
                        "Invoking LLM (sync) using %s:%s (attempt %d/%d)",
                        provider, model, attempt, max_attempts,
                    )
                    with _sync_gate():
                        response = client.invoke(messages, **kwargs)
                    latency_ms = int((time.time() - start_time) * 1000)

                    self._enrich_response_metadata(response, provider, model, latency_ms)
                    logger.info("LLM Success (sync) via %s:%s in %dms", provider, model, latency_ms)
                    return response
                except Exception as exc:
                    latency_ms = int((time.time() - start_time) * 1000)
                    if not self._is_retryable_error(exc) or attempt == max_attempts:
                        logger.warning("Failed LLM attempt (sync) using %s:%s in %dms: %s", provider, model, latency_ms, exc)
                        last_error = exc
                        if not self._is_retryable_error(exc):
                            raise exc
                        break  # move to next provider
                    delay = _retry_delay_seconds(exc, attempt)
                    logger.warning(
                        "Retryable error on %s:%s (attempt %d): %s. Retrying in %.2fs...",
                        provider, model, attempt, exc, delay,
                    )
                    time.sleep(delay)

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

            max_attempts = 1 + max(0, int(getattr(settings, "LLM_MAX_RETRIES", 2)))
            for attempt in range(1, max_attempts + 1):
                start_time = time.time()
                try:
                    logger.info(
                        "Invoking LLM (async) using %s:%s (attempt %d/%d)",
                        provider, model, attempt, max_attempts,
                    )
                    async with _async_gate():
                        response = await client.ainvoke(messages, **kwargs)
                    latency_ms = int((time.time() - start_time) * 1000)

                    self._enrich_response_metadata(response, provider, model, latency_ms)
                    logger.info("LLM Success (async) via %s:%s in %dms", provider, model, latency_ms)
                    return response
                except Exception as exc:
                    latency_ms = int((time.time() - start_time) * 1000)
                    if not self._is_retryable_error(exc) or attempt == max_attempts:
                        logger.warning("Failed LLM attempt (async) using %s:%s in %dms: %s", provider, model, latency_ms, exc)
                        last_error = exc
                        if not self._is_retryable_error(exc):
                            raise exc
                        break  # move to next provider
                    delay = _retry_delay_seconds(exc, attempt)
                    logger.warning(
                        "Retryable error on %s:%s (attempt %d): %s. Retrying in %.2fs...",
                        provider, model, attempt, exc, delay,
                    )
                    await asyncio.sleep(delay)

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
    """Return the common resilient, rate-limited reasoning client.

    Every node uses the same concurrency and retry policy, whether or not a
    fallback provider is configured.
    """
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider not in ("openrouter", "openai", "groq"):
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")

    return ResilientLLMClient(primary_provider=provider, model_name=model_name)
