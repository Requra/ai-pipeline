import pytest
import os
import time
import asyncio
import json
import httpx
import openai
from unittest.mock import MagicMock, AsyncMock, patch
from app.config import settings, llm_key_for, embedding_key_for
from app.llm import ResilientLLMClient, get_llm, ITIChatClient, ITIChatResponse
from app.rag.embeddings import get_embedder, ITIEmbedder
from app.providers.iti_models import ITI_CHAT_MODELS, ITI_EMBEDDING_MODELS

# 1. Config Loading & Key retrieval
def test_iti_config_loading(monkeypatch):
    monkeypatch.setattr(settings, "ITI_API_KEY", "test-iti-key-123")
    monkeypatch.setattr(settings, "ITI_BASE_URL", "http://test-gateway.iti.net.eg")
    monkeypatch.setattr(settings, "ITI_PRIMARY_MODEL", "nvidia.nemotron-super-3-120b")
    
    assert settings.ITI_API_KEY == "test-iti-key-123"
    assert settings.ITI_BASE_URL == "http://test-gateway.iti.net.eg"
    assert settings.ITI_PRIMARY_MODEL == "nvidia.nemotron-super-3-120b"
    assert llm_key_for("iti") == "test-iti-key-123"
    assert embedding_key_for("iti") == "test-iti-key-123"

def test_missing_key_handling(monkeypatch):
    monkeypatch.setattr(settings, "ITI_API_KEY", None)
    assert llm_key_for("iti") is None
    assert embedding_key_for("iti") is None

# 2. Secret Redaction
def test_secret_redaction():
    # Make sure we don't accidentally leak key in string representation or errors
    client = ITIChatClient(model="nvidia.nemotron-super-3-120b")
    # Verify the API key is not printed in repr or str
    client_str = str(client)
    client_repr = repr(client)
    assert "test-iti-key-123" not in client_str
    assert "sbg_" not in client_str
    assert "test-iti-key-123" not in client_repr
    assert "sbg_" not in client_repr

# 3. Chat Payload Conversion
def test_chat_payload_conversion():
    client = ITIChatClient(model="nvidia.nemotron-super-3-120b")
    
    # LangChain messages
    class MockMessage:
        def __init__(self, type, content):
            self.type = type
            self.content = content
            
    messages = [
        MockMessage("system", "System instruction"),
        MockMessage("human", "Hello user"),
        MockMessage("ai", "Hello back"),
        {"role": "user", "content": "dict format user"},
        ("assistant", "tuple format assistant")
    ]
    
    system_prompt, converted = client._convert_messages(messages)
    assert system_prompt == "System instruction"
    assert len(converted) == 4
    assert converted[0] == {"role": "user", "content": "Hello user"}
    assert converted[1] == {"role": "assistant", "content": "Hello back"}
    assert converted[2] == {"role": "user", "content": "dict format user"}
    assert converted[3] == {"role": "assistant", "content": "tuple format assistant"}

# 4. Response Parsing
def test_response_parsing():
    raw_response = {
        "output_text": "Response from Nemotron",
        "model_id": "nvidia.nemotron-super-3-120b",
        "region": "us-east-1",
        "status": "completed",
        "actual_cost_usd": 0.0001,
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }
    parsed = ITIChatResponse(raw_response)
    assert parsed.content == "Response from Nemotron"
    assert parsed.response_metadata["model"] == "nvidia.nemotron-super-3-120b"
    assert parsed.response_metadata["region"] == "us-east-1"
    assert parsed.response_metadata["status"] == "completed"
    assert parsed.response_metadata["actual_cost_usd"] == 0.0001
    assert parsed.usage_metadata == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

# 5. Client Invocation (Sync & Async)
@patch("httpx.Client")
def test_iti_chat_invoke(mock_client_class, monkeypatch):
    monkeypatch.setattr(settings, "ITI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ITI_BASE_URL", "http://test")
    
    mock_client = MagicMock()
    mock_client_class.return_value.__enter__.return_value = mock_client
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "output_text": "OK",
        "model_id": "nvidia.nemotron-super-3-120b",
        "status": "completed"
    }
    mock_client.post.return_value = mock_resp
    
    client = ITIChatClient(model="nvidia.nemotron-super-3-120b")
    res = client.invoke([("human", "test")], temperature=0, max_tokens=10)
    assert res.content == "OK"
    mock_client.post.assert_called_once()
    
    # Verify exact endpoint URL in call
    args, kwargs = mock_client.post.call_args
    assert args[0] == "http://test/api/v1/student/chat"
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["json"]["temperature"] == 0
    assert kwargs["json"]["max_tokens"] == 10

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_iti_chat_ainvoke(mock_async_client_class, monkeypatch):
    monkeypatch.setattr(settings, "ITI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ITI_BASE_URL", "http://test")
    
    mock_client = MagicMock()
    mock_async_client_class.return_value.__aenter__.return_value = mock_client
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "output_text": "OK Async",
        "model_id": "nvidia.nemotron-super-3-120b"
    }
    mock_client.post = AsyncMock(return_value=mock_resp)
    
    client = ITIChatClient(model="nvidia.nemotron-super-3-120b")
    res = await client.ainvoke([("human", "test")])
    assert res.content == "OK Async"
    mock_client.post.assert_called_once()

# 6. Retry & Fallback Routing Errors
@patch("httpx.Client")
def test_iti_chat_403_non_retryable(mock_client_class, monkeypatch):
    mock_client = MagicMock()
    mock_client_class.return_value.__enter__.return_value = mock_client
    
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.text = '{"error":{"code":"MODEL_OUTSIDE_ADMIN_CEILING"}}'
    mock_resp.json.return_value = {"error": {"code": "MODEL_OUTSIDE_ADMIN_CEILING"}}
    mock_client.post.return_value = mock_resp
    
    client = ITIChatClient(model="nvidia.nemotron-super-3-120b")
    
    # 403 Forbidden is a PermissionDeniedError and should not be retryable
    with pytest.raises(openai.PermissionDeniedError):
        client.invoke([("human", "test")])

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_iti_chat_500_retryable(mock_async_client_class, monkeypatch):
    # Test that 500 error raises HTTPStatusError which triggers retry/fallback in ResilientLLMClient
    monkeypatch.setattr(settings, "LLM_PROVIDER", "iti")
    monkeypatch.setattr(settings, "ITI_PRIMARY_MODEL", "nvidia.nemotron-super-3-120b")
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", '[{"provider":"iti","model":"openai.gpt-oss-120b-1:0"}]')
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(time, "sleep", lambda x: None)
    
    mock_client = MagicMock()
    mock_async_client_class.return_value.__aenter__.return_value = mock_client
    
    # Primary fails with 500
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_resp_500.raise_for_status.side_effect = httpx.HTTPStatusError("500 Internal Error", request=MagicMock(), response=mock_resp_500)
    
    # Fallback succeeds
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = {"output_text": "fallback output", "model_id": "openai.gpt-oss-120b-1:0"}
    
    mock_client.post = AsyncMock(side_effect=[mock_resp_500, mock_resp_500, mock_resp_ok])
    
    resilient_client = ResilientLLMClient(primary_provider="iti")
    assert len(resilient_client.providers) == 2
    
    # Ainvoke should attempt primary (1 initial + 1 retry) then fallback
    res = await resilient_client.ainvoke([("human", "hello")])
    assert res.content == "fallback output"
    assert mock_client.post.call_count == 3

# 7. Same-Provider Fallback Deduplication
def test_fallback_deduplication(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "iti")
    monkeypatch.setattr(settings, "ITI_PRIMARY_MODEL", "nvidia.nemotron-super-3-120b")
    # Fallback list has same provider (iti) and duplicate entries
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", json.dumps([
        {"provider": "iti", "model": "openai.gpt-oss-120b-1:0"},
        {"provider": "iti", "model": "openai.gpt-oss-120b-1:0"}, # duplicate
        {"provider": "groq", "model": "llama-3.3-70b-versatile"}
    ]))
    
    client = ResilientLLMClient(primary_provider="iti")
    # Verify it has exactly 3 unique provider-model pairs
    assert len(client.providers) == 3
    assert client.providers[0] == {"provider": "iti", "model": "nvidia.nemotron-super-3-120b"}
    assert client.providers[1] == {"provider": "iti", "model": "openai.gpt-oss-120b-1:0"}
    assert client.providers[2] == {"provider": "groq", "model": "llama-3.3-70b-versatile"}


def test_iti_default_fallback_model_is_used_when_chain_is_not_overridden(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "iti")
    monkeypatch.setattr(settings, "ITI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ITI_PRIMARY_MODEL", "nvidia.nemotron-super-3-120b")
    monkeypatch.setattr(settings, "ITI_FALLBACK_MODEL", "openai.gpt-oss-120b-1:0")
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", None)

    client = ResilientLLMClient(primary_provider="iti")

    assert client.providers == [
        {"provider": "iti", "model": "nvidia.nemotron-super-3-120b"},
        {"provider": "iti", "model": "openai.gpt-oss-120b-1:0"},
    ]


def test_iti_same_provider_three_model_fallback_chain(monkeypatch):
    monkeypatch.setattr(settings, "ITI_PRIMARY_MODEL", "nvidia.nemotron-super-3-120b")
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", json.dumps([
        {"provider": "iti", "model": "openai.gpt-oss-120b-1:0"},
        {"provider": "iti", "model": "mistral.mistral-large-3-675b-instruct"},
        {"provider": "iti", "model": "nvidia.nemotron-super-3-120b"},
    ]))

    client = ResilientLLMClient(primary_provider="iti")

    assert client.providers == [
        {"provider": "iti", "model": "nvidia.nemotron-super-3-120b"},
        {"provider": "iti", "model": "openai.gpt-oss-120b-1:0"},
        {"provider": "iti", "model": "mistral.mistral-large-3-675b-instruct"},
    ]

# 8. Embeddings (Single & Batch)
@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_iti_embed_single_and_batch(mock_async_client_class, monkeypatch):
    monkeypatch.setattr(settings, "ITI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ITI_BASE_URL", "http://test")
    
    mock_client = MagicMock()
    mock_async_client_class.return_value.__aenter__.return_value = mock_client
    
    # Mock single embed response
    mock_resp_single = MagicMock()
    mock_resp_single.status_code = 200
    mock_resp_single.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
    
    # Mock batch embed response
    mock_resp_batch = MagicMock()
    mock_resp_batch.status_code = 200
    mock_resp_batch.json.return_value = {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}
    
    mock_client.post = AsyncMock(side_effect=[mock_resp_single, mock_resp_batch])
    
    embedder = ITIEmbedder(model="amazon.titan-embed-text-v1", api_key="test-key")
    
    # Test embed_query
    q_vec = await embedder.embed_query("test query")
    assert q_vec == [0.1, 0.2, 0.3]
    
    # Test embed_documents
    docs_vecs = await embedder.embed_documents(["doc1", "doc2"])
    assert docs_vecs == [[0.1, 0.2], [0.3, 0.4]]


def test_get_iti_embedder_uses_approved_titan_model(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_EMBEDDINGS", True)
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "iti")
    monkeypatch.setattr(settings, "ITI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "unrelated-model")
    monkeypatch.setattr(settings, "ITI_EMBEDDING_MODEL", "amazon.titan-embed-text-v1")

    embedder = get_embedder()

    assert isinstance(embedder, ITIEmbedder)
    assert embedder.model == "amazon.titan-embed-text-v1"

# 9. Embedding Invalid Response
@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_iti_embed_invalid_response(mock_async_client_class, monkeypatch):
    mock_client = MagicMock()
    mock_async_client_class.return_value.__aenter__.return_value = mock_client
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"wrong_key": "not embedding"}
    mock_client.post = AsyncMock(return_value=mock_resp)
    
    embedder = ITIEmbedder(model="amazon.titan-embed-text-v1", api_key="test-key")
    
    with pytest.raises(ValueError, match="No embedding found in response"):
        await embedder.embed_documents(["doc"])

# 10. Existing providers regression check
def test_existing_providers_work(monkeypatch):
    # Verify openrouter/groq/openai paths still compile and work as expected
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "key-or")
    client = get_llm()
    assert client.primary_provider == "openrouter"
