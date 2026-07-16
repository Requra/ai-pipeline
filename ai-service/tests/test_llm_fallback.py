import pytest
import time
import httpx
import openai
from unittest.mock import MagicMock
from app.llm import ResilientLLMClient
from app.config import settings


class MockAIMessage:
    def __init__(self, content, usage_metadata=None):
        self.content = content
        self.response_metadata = {}
        if usage_metadata:
            self.usage_metadata = usage_metadata


def test_resilient_llm_client_fallback(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
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


def test_resilient_llm_client_non_retryable_error(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
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

    def mock_instantiate(provider, model):
        if provider == "openrouter":
            return mock_primary
        return mock_fallback

    monkeypatch.setattr(client, "_instantiate_client", mock_instantiate)

    with pytest.raises(openai.AuthenticationError):
        client.invoke("hello")

    # Non-retryable error should fail immediately without retries or fallbacks
    assert mock_primary.invoke.call_count == 1
    assert mock_fallback.invoke.call_count == 0
