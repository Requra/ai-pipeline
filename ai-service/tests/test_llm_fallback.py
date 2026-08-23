import pytest
import time
import asyncio
import httpx
import openai
from unittest.mock import AsyncMock, MagicMock
from app.llm import (
    ResilientLLMClient,
    _is_permanent_quota_error,
    _quota_blocked_until,
    _retry_delay_seconds,
)
from app.config import settings


class MockAIMessage:
    def __init__(self, content, usage_metadata=None):
        self.content = content
        self.response_metadata = {}
        if usage_metadata:
            self.usage_metadata = usage_metadata


@pytest.fixture(autouse=True)
def _clear_quota_cooldown():
    _quota_blocked_until.clear()
    yield
    _quota_blocked_until.clear()


def test_resilient_llm_client_fallback(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", '[{"provider":"groq","model":"llama-3.3-70b-versatile"}]')

    client = ResilientLLMClient(primary_provider="openrouter")
    assert len(client.providers) == 2
    assert client.providers[0]["provider"] == "openrouter"
    assert client.providers[1]["provider"] == "groq"

    # Mock primary client to throw RateLimitError on all attempts
    mock_primary = MagicMock()
    mock_primary.invoke.side_effect = openai.RateLimitError(
        message="Rate limit exceeded",
        response=httpx.Response(429, request=httpx.Request("POST", "http://test")),
        body=None
    )

    # Mock fallback client to succeed
    mock_fallback = MagicMock()
    mock_fallback.invoke.return_value = MockAIMessage(
        content="Success from fallback",
        usage_metadata={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    )

    def mock_instantiate(provider, model):
        if provider == "openrouter":
            return mock_primary
        return mock_fallback

    monkeypatch.setattr(client, "_instantiate_client", mock_instantiate)
    
    # Inject mock sleep to keep tests fast
    monkeypatch.setattr(time, "sleep", lambda x: None)

    response = client.invoke("hello")
    assert response.content == "Success from fallback"
    assert response.response_metadata["provider"] == "groq"
    assert response.response_metadata["model"] == "llama-3.3-70b-versatile"
    assert "latency_ms" in response.response_metadata
    assert response.response_metadata["token_usage"]["total_tokens"] == 15

    # Should attempt primary 3 times (1 initial + 2 retries) before falling back
    assert mock_primary.invoke.call_count == 3
    assert mock_fallback.invoke.call_count == 1


def test_resilient_llm_client_non_retryable_error_uses_configured_fallback(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", '[{"provider":"groq","model":"llama-3.3-70b-versatile"}]')

    client = ResilientLLMClient(primary_provider="openrouter")

    # Mock primary client to throw AuthenticationError (non-retryable)
    mock_primary = MagicMock()
    mock_primary.invoke.side_effect = openai.AuthenticationError(
        message="Invalid API Key",
        response=httpx.Response(401, request=httpx.Request("POST", "http://test")),
        body=None
    )

    mock_fallback = MagicMock()
    mock_fallback.invoke.return_value = MockAIMessage(content="fallback after auth failure")

    def mock_instantiate(provider, model):
        if provider == "openrouter":
            return mock_primary
        return mock_fallback

    monkeypatch.setattr(client, "_instantiate_client", mock_instantiate)

    response = client.invoke("hello")

    # Non-retryable error should fail immediately without a retry, but a
    # configured, credentialed fallback may still complete the request.
    assert response.content == "fallback after auth failure"
    assert mock_primary.invoke.call_count == 1
    assert mock_fallback.invoke.call_count == 1


def test_resilient_llm_client_ignores_fallback_without_credential(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", None)
    monkeypatch.setattr(
        settings,
        "LLM_FALLBACK_CHAIN",
        '[{"provider":"groq","model":"llama-3.3-70b-versatile"}]',
    )

    client = ResilientLLMClient(primary_provider="openrouter")

    assert client.providers == [{"provider": "openrouter", "model": settings.OPENROUTER_MODEL}]


@pytest.mark.asyncio
async def test_async_non_retryable_error_uses_configured_fallback(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(
        settings,
        "LLM_FALLBACK_CHAIN",
        '[{"provider":"groq","model":"llama-3.3-70b-versatile"}]',
    )
    client = ResilientLLMClient(primary_provider="openrouter")

    primary = MagicMock()
    primary.ainvoke = AsyncMock(
        side_effect=openai.AuthenticationError(
            message="Invalid API key",
            response=httpx.Response(401, request=httpx.Request("POST", "http://test")),
            body=None,
        )
    )
    fallback = MagicMock()
    fallback.ainvoke = AsyncMock(return_value=MockAIMessage(content="async fallback"))
    monkeypatch.setattr(
        client,
        "_instantiate_client",
        lambda provider, _model: primary if provider == "openrouter" else fallback,
    )

    response = await client.ainvoke("hello")

    assert response.content == "async fallback"
    assert primary.ainvoke.await_count == 1
    assert fallback.ainvoke.await_count == 1


def test_exhausted_token_quota_skips_retries_and_uses_fallback(monkeypatch):
    monkeypatch.setattr(
        settings,
        "LLM_FALLBACK_CHAIN",
        '[{"provider":"groq","model":"llama-3.3-70b-versatile"}]',
    )
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 5)
    monkeypatch.setattr(settings, "LLM_QUOTA_COOLDOWN_SECONDS", 0)
    client = ResilientLLMClient(primary_provider="openrouter")
    exhausted = openai.RateLimitError(
        message="No remaining tokens; insufficient credits",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "http://test"),
        ),
        body={"error": {"code": "insufficient_quota"}},
    )
    primary = MagicMock()
    primary.invoke.side_effect = exhausted
    fallback = MagicMock()
    fallback.invoke.return_value = MockAIMessage(content="fallback succeeded")

    monkeypatch.setattr(
        client,
        "_instantiate_client",
        lambda provider, _model: primary if provider == "openrouter" else fallback,
    )
    monkeypatch.setattr(time, "sleep", lambda _delay: pytest.fail("quota must not back off"))

    response = client.invoke("hello")

    assert _is_permanent_quota_error(exhausted)
    assert response.content == "fallback succeeded"
    assert primary.invoke.call_count == 1
    assert fallback.invoke.call_count == 1


def test_payment_required_is_permanent_quota_error():
    error = openai.APIStatusError(
        message="Payment required",
        response=httpx.Response(
            402,
            request=httpx.Request("POST", "http://test"),
        ),
        body=None,
    )
    assert _is_permanent_quota_error(error)


def test_quota_circuit_skips_exhausted_provider_across_clients(monkeypatch):
    model = "quota-circuit-test-model"
    key = ("openrouter", model)
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", None)
    monkeypatch.setattr(settings, "LLM_QUOTA_COOLDOWN_SECONDS", 300)
    exhausted = openai.RateLimitError(
        message="Quota exceeded; no remaining tokens",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "http://test"),
        ),
        body=None,
    )
    provider_client = MagicMock()
    provider_client.invoke.side_effect = exhausted
    first = ResilientLLMClient(primary_provider="openrouter", model_name=model)
    monkeypatch.setattr(first, "_instantiate_client", lambda _provider, _model: provider_client)

    try:
        with pytest.raises(RuntimeError, match="All configured LLM providers failed"):
            first.invoke("hello")
        assert provider_client.invoke.call_count == 1

        second = ResilientLLMClient(primary_provider="openrouter", model_name=model)
        instantiate = MagicMock()
        monkeypatch.setattr(second, "_instantiate_client", instantiate)
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            second.invoke("hello")
        instantiate.assert_not_called()
    finally:
        _quota_blocked_until.pop(key, None)


def test_retry_delay_respects_retry_after_and_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_SECONDS", 1.0)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_SECONDS", 30.0)
    monkeypatch.setattr("app.llm.random.uniform", lambda _start, _end: 0.0)
    error = openai.RateLimitError(
        message="Rate limit exceeded",
        response=httpx.Response(
            429,
            headers={"Retry-After": "7"},
            request=httpx.Request("POST", "http://test"),
        ),
        body=None,
    )

    assert _retry_delay_seconds(error, attempt=1) == 7.0
    assert _retry_delay_seconds(error, attempt=10) == 30.0


@pytest.mark.asyncio
async def test_async_calls_share_configured_concurrency_limit(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", None)
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 0)
    client = ResilientLLMClient(primary_provider="openrouter")

    class TrackingClient:
        def __init__(self):
            self.active = 0
            self.maximum_active = 0

        async def ainvoke(self, _messages, **_kwargs):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return MockAIMessage(content="ok")

    provider_client = TrackingClient()
    monkeypatch.setattr(
        client,
        "_instantiate_client",
        lambda _provider, _model: provider_client,
    )

    results = await asyncio.gather(*(client.ainvoke(f"request-{i}") for i in range(6)))

    assert [result.content for result in results] == ["ok"] * 6
    assert provider_client.maximum_active == 2


@pytest.mark.asyncio
async def test_async_retry_uses_configured_backoff(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", None)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_SECONDS", 1.0)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_SECONDS", 30.0)
    monkeypatch.setattr("app.llm.random.uniform", lambda _start, _end: 0.0)
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    client = ResilientLLMClient(primary_provider="openrouter")
    outcomes = iter([
        openai.RateLimitError(
            message="Rate limit exceeded",
            response=httpx.Response(
                429,
                request=httpx.Request("POST", "http://test"),
            ),
            body=None,
        ),
        openai.RateLimitError(
            message="Rate limit exceeded",
            response=httpx.Response(
                429,
                request=httpx.Request("POST", "http://test"),
            ),
            body=None,
        ),
        MockAIMessage(content="recovered"),
    ])

    class RetryClient:
        async def ainvoke(self, _messages, **_kwargs):
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

    provider_client = RetryClient()
    monkeypatch.setattr(
        client,
        "_instantiate_client",
        lambda _provider, _model: provider_client,
    )

    response = await client.ainvoke("hello")

    assert response.content == "recovered"
    assert delays == [1.0, 2.0]
