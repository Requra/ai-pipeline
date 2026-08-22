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
from langchain_openai import ChatOpenAI
from app.config import settings

logger = logging.getLogger(__name__)

_gate_lock = threading.Lock()
_sync_gates: Dict[int, threading.BoundedSemaphore] = {}
_quota_blocked_until: Dict[tuple[str, str], float] = {}
_async_gates: Dict[int, Dict[int, asyncio.Semaphore]] = {}


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
    loop_id = id(loop)
    limit = _configured_concurrency()
    with _gate_lock:
        gates_for_loop = _async_gates.setdefault(loop_id, {})
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


_PERMANENT_QUOTA_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "insufficient credits",
    "not enough credits",
    "requires more credits",
    "can only afford",
    "credit balance",
    "credits exhausted",
    "out of credits",
    "no remaining tokens",
    "token quota",
    "token limit exceeded",
    "token limit for this account",
    "tokens left",
    "quota exceeded",
    "daily quota",
    "monthly quota",
    "usage limit reached",
    "free-models-per-day",
    "tokens per day",
    "tpd",
    "payment required",
)


def _error_text(exc: Exception) -> str:
    """Collect provider details without depending on one SDK error shape."""
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    response = getattr(exc, "response", None)
    response_text = getattr(response, "text", None)
    if response_text:
        parts.append(str(response_text))
    return " ".join(parts).lower()


def _is_permanent_quota_error(exc: Exception) -> bool:
    """Identify exhausted credits or daily/monthly token quota, which backoff cannot fix."""
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 402:
        return True
    details = _error_text(exc)
    # Transient per-minute (TPM/RPM) rate limits must backoff and retry, not trigger permanent lockout
    if (
        "tokens per minute" in details
        or "tpm" in details
        or "requests per minute" in details
        or "rpm" in details
    ):
        return False
    return any(marker in details for marker in _PERMANENT_QUOTA_MARKERS)


def _mark_quota_exhausted(provider: str, model: str) -> None:
    cooldown = max(
        0.0,
        float(getattr(settings, "LLM_QUOTA_COOLDOWN_SECONDS", 300.0)),
    )
    with _gate_lock:
        _quota_blocked_until[(provider, model)] = time.monotonic() + cooldown


def _quota_cooldown_remaining(provider: str, model: str) -> float:
    key = (provider, model)
    with _gate_lock:
        blocked_until = _quota_blocked_until.get(key, 0.0)
        remaining = blocked_until - time.monotonic()
        if remaining <= 0:
            _quota_blocked_until.pop(key, None)
            return 0.0
        return remaining


class ITIChatResponse:
    def __init__(self, data: dict):
        self.content = data.get("output_text", "")
        usage = data.get("usage") or {}
        self.response_metadata = {
            "request_id": data.get("request_id"),
            "model": data.get("model_id"),
            "region": data.get("region"),
            "status": data.get("status"),
            "estimated_cost_usd": data.get("estimated_cost_usd"),
            "actual_cost_usd": data.get("actual_cost_usd"),
        }
        self.usage_metadata = {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

class ITIChatClient:
    def __init__(self, model: str):
        self.model = model
        base = settings.ITI_BASE_URL.rstrip("/")
        if base.endswith("/student"):
            base = base[:-8]
        if "/api/v1" not in base:
            base = f"{base}/api/v1"
        self.base_url = base
        self.api_key = settings.ITI_API_KEY
        self.timeout = settings.PROVIDER_TIMEOUT_SECONDS

    def _convert_messages(self, messages):
        system_prompt = None
        converted_messages = []
        for msg in messages:
            role = None
            content = ""
            if hasattr(msg, "type"):
                role = msg.type
                content = msg.content
            elif isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content", "")
            elif isinstance(msg, tuple) and len(msg) == 2:
                role, content = msg
                
            if not role:
                continue
                
            role_lower = role.lower()
            if role_lower == "system":
                system_prompt = content
            elif role_lower in ("human", "user"):
                converted_messages.append({"role": "user", "content": content})
            elif role_lower in ("ai", "assistant"):
                converted_messages.append({"role": "assistant", "content": content})
                
        return system_prompt, converted_messages

    def invoke(self, messages, **kwargs):
        import httpx
        system_prompt, converted_messages = self._convert_messages(messages)
        payload = {
            "model_id": self.model,
            "messages": converted_messages
        }
        if system_prompt:
            payload["system_prompt"] = system_prompt
        for field in ("temperature", "max_tokens"):
            if kwargs.get(field) is not None:
                payload[field] = kwargs[field]
            
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        url = f"{self.base_url}/student/chat"
        
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code == 403:
                raise openai.PermissionDeniedError(
                    message=resp.text,
                    response=resp,
                    body=resp.json() if resp.headers.get("content-type") == "application/json" else None
                )
            resp.raise_for_status()
            res = resp.json()
            return ITIChatResponse(res)

    async def ainvoke(self, messages, **kwargs):
        import httpx
        system_prompt, converted_messages = self._convert_messages(messages)
        payload = {
            "model_id": self.model,
            "messages": converted_messages
        }
        if system_prompt:
            payload["system_prompt"] = system_prompt
        for field in ("temperature", "max_tokens"):
            if kwargs.get(field) is not None:
                payload[field] = kwargs[field]
            
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        url = f"{self.base_url}/student/chat"
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 403:
                raise openai.PermissionDeniedError(
                    message=resp.text,
                    response=resp,
                    body=resp.json() if resp.headers.get("content-type") == "application/json" else None
                )
            resp.raise_for_status()
            res = resp.json()
            return ITIChatResponse(res)

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
        self.providers.append({"provider": primary_provider, "model": primary_model})

        seen = {(primary_provider, primary_model)}

        # Add fallback providers from configuration
        for fb in self.fallback_chain:
            fb_provider = fb.get("provider")
            fb_model = fb.get("model")
            if fb_provider and fb_model:
                key = (fb_provider, fb_model)
                if key not in seen:
                    seen.add(key)
                    self.providers.append({"provider": fb_provider, "model": fb_model})

    def _get_default_model(
        self, provider: str, override_model: Optional[str] = None
    ) -> str:
        if provider == "openrouter":
            return override_model or settings.OPENROUTER_MODEL or "openai/gpt-4o-mini"
        elif provider == "groq":
            return override_model or settings.GROQ_MODEL or "openai/gpt-oss-20b"
        elif provider == "iti":
            return override_model or settings.ITI_PRIMARY_MODEL
        else:
            return override_model or settings.OPENAI_MODEL or "gpt-4o-mini"

    def _instantiate_client(self, provider: str, model: str):
        if provider == "iti":
            return ITIChatClient(model=model)
        elif provider == "openrouter":
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
                max_tokens=2048,
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
        if _is_permanent_quota_error(exc):
            return False
        if isinstance(
            exc,
            (
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.InternalServerError,
                httpx.HTTPError,
                asyncio.TimeoutError,
                TimeoutError,
            ),
        ):
            return True
        # Support name-matching for cases where imports might differ dynamically
        cls_name = exc.__class__.__name__
        if cls_name in (
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
            "InternalServerError",
            "HTTPError",
            "TimeoutError",
        ):
            return True
        return False

    def invoke(self, messages, **kwargs):
        last_error = None
        for config in self.providers:
            provider = config["provider"]
            model = config["model"]
            cooldown = _quota_cooldown_remaining(provider, model)
            if cooldown > 0:
                logger.warning(
                    "Skipping quota-exhausted provider %s:%s for another %.1fs",
                    provider,
                    model,
                    cooldown,
                )
                last_error = RuntimeError(
                    f"Provider {provider}:{model} is temporarily unavailable after quota exhaustion"
                )
                continue

            try:
                client = self._instantiate_client(provider, model)
            except Exception as e:
                logger.warning(
                    "Failed to instantiate client for %s:%s: %s", provider, model, e
                )
                last_error = e
                continue

            max_attempts = 1 + max(0, int(getattr(settings, "LLM_MAX_RETRIES", 2)))
            for attempt in range(1, max_attempts + 1):
                start_time = time.time()
                try:
                    logger.info(
                        "Invoking LLM (sync) using %s:%s (attempt %d/%d)",
                        provider,
                        model,
                        attempt,
                        max_attempts,
                    )
                    with _sync_gate():
                        response = client.invoke(messages, **kwargs)
                    latency_ms = int((time.time() - start_time) * 1000)

                    self._enrich_response_metadata(
                        response, provider, model, latency_ms
                    )
                    logger.info(
                        "LLM Success (sync) via %s:%s in %dms",
                        provider,
                        model,
                        latency_ms,
                    )
                    return response
                except Exception as exc:
                    latency_ms = int((time.time() - start_time) * 1000)
                    if _is_permanent_quota_error(exc):
                        _mark_quota_exhausted(provider, model)
                        logger.warning(
                            "Permanent quota exhaustion on %s:%s; skipping retries and trying the next configured provider",
                            provider,
                            model,
                        )
                        last_error = exc
                        break
                    if not self._is_retryable_error(exc) or attempt == max_attempts:
                        logger.warning(
                            "Failed LLM attempt (sync) using %s:%s in %dms: %s",
                            provider,
                            model,
                            latency_ms,
                            exc,
                        )
                        last_error = exc
                        if not self._is_retryable_error(exc):
                            raise exc
                        break  # move to next provider
                    delay = _retry_delay_seconds(exc, attempt)
                    logger.warning(
                        "Retryable error on %s:%s (attempt %d): %s. Retrying in %.2fs...",
                        provider,
                        model,
                        attempt,
                        exc,
                        delay,
                    )
                    time.sleep(delay)

        raise RuntimeError(
            f"All configured LLM providers failed. Last error: {last_error}"
        ) from last_error

    async def ainvoke(self, messages, **kwargs):
        last_error = None
        for config in self.providers:
            provider = config["provider"]
            model = config["model"]
            cooldown = _quota_cooldown_remaining(provider, model)
            if cooldown > 0:
                logger.warning(
                    "Skipping quota-exhausted provider %s:%s for another %.1fs",
                    provider,
                    model,
                    cooldown,
                )
                last_error = RuntimeError(
                    f"Provider {provider}:{model} is temporarily unavailable after quota exhaustion"
                )
                continue

            try:
                client = self._instantiate_client(provider, model)
            except Exception as e:
                logger.warning(
                    "Failed to instantiate client for %s:%s: %s", provider, model, e
                )
                last_error = e
                continue

            max_attempts = 1 + max(0, int(getattr(settings, "LLM_MAX_RETRIES", 2)))
            for attempt in range(1, max_attempts + 1):
                start_time = time.time()
                try:
                    logger.info(
                        "Invoking LLM (async) using %s:%s (attempt %d/%d)",
                        provider,
                        model,
                        attempt,
                        max_attempts,
                    )
                    async with _async_gate():
                        response = await client.ainvoke(messages, **kwargs)
                    latency_ms = int((time.time() - start_time) * 1000)

                    self._enrich_response_metadata(
                        response, provider, model, latency_ms
                    )
                    logger.info(
                        "LLM Success (async) via %s:%s in %dms",
                        provider,
                        model,
                        latency_ms,
                    )
                    return response
                except Exception as exc:
                    latency_ms = int((time.time() - start_time) * 1000)
                    if _is_permanent_quota_error(exc):
                        _mark_quota_exhausted(provider, model)
                        logger.warning(
                            "Permanent quota exhaustion on %s:%s; skipping retries and trying the next configured provider",
                            provider,
                            model,
                        )
                        last_error = exc
                        break
                    if not self._is_retryable_error(exc) or attempt == max_attempts:
                        logger.warning(
                            "Failed LLM attempt (async) using %s:%s in %dms: %s",
                            provider,
                            model,
                            latency_ms,
                            exc,
                        )
                        last_error = exc
                        if not self._is_retryable_error(exc):
                            raise exc
                        break  # move to next provider
                    delay = _retry_delay_seconds(exc, attempt)
                    logger.warning(
                        "Retryable error on %s:%s (attempt %d): %s. Retrying in %.2fs...",
                        provider,
                        model,
                        attempt,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"All configured LLM providers failed. Last error: {last_error}"
        ) from last_error

    def _enrich_response_metadata(
        self, response: Any, provider: str, model: str, latency_ms: int
    ):
        if (
            not hasattr(response, "response_metadata")
            or response.response_metadata is None
        ):
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
                "prompt_tokens": token_usage.get("prompt_tokens")
                or token_usage.get("input_tokens")
                or 0,
                "completion_tokens": token_usage.get("completion_tokens")
                or token_usage.get("output_tokens")
                or 0,
                "total_tokens": token_usage.get("total_tokens") or 0,
            }


def get_llm(model_name: Optional[str] = None):
    """Return the common resilient, rate-limited reasoning client.

    Every node uses the same concurrency and retry policy, whether or not a
    fallback provider is configured.
    """
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider not in ("openrouter", "openai", "groq", "iti"):
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")

    return ResilientLLMClient(primary_provider=provider, model_name=model_name)
