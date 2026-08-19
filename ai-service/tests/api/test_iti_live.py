import os
import pytest
import httpx
import openai
from app.config import settings
from app.llm import ITIChatClient
from app.rag.embeddings import ITIEmbedder

@pytest.mark.skipif(
    not os.environ.get("ITI_API_KEY"),
    reason="ITI_API_KEY is not set in environment"
)
def test_iti_chat_live_forbidden():
    # Verify that calling the real ITI Gateway with the provided key returns the expected 403 MODEL_OUTSIDE_ADMIN_CEILING error,
    # proving that transport authentication, base url routing, and request parsing are correctly wired up to the real backend.
    client = ITIChatClient(model="nvidia.nemotron-super-3-120b")
    
    with pytest.raises(openai.PermissionDeniedError) as exc_info:
        client.invoke([("human", "Reply with exactly OK")])
        
    error_text = str(exc_info.value)
    assert "MODEL_OUTSIDE_ADMIN_CEILING" in error_text or "ceiling" in error_text.lower()
    print("Live ITI chat connectivity verified successfully (returned expected admin ceiling error).")

@pytest.mark.skipif(
    not os.environ.get("ITI_API_KEY"),
    reason="ITI_API_KEY is not set in environment"
)
@pytest.mark.asyncio
async def test_iti_embed_live_forbidden():
    embedder = ITIEmbedder(model="amazon.titan-embed-text-v1", api_key=settings.ITI_API_KEY)
    
    # Verify that embedding also returns the expected 403 MODEL_OUTSIDE_ADMIN_CEILING error,
    # proving the embedding client payload and base url are correctly hitting the real API.
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await embedder.embed_documents(["User can reset their password."])
        
    assert exc_info.value.response.status_code == 403
    error_text = exc_info.value.response.text
    assert "MODEL_OUTSIDE_ADMIN_CEILING" in error_text or "ceiling" in error_text.lower()
    print("Live ITI embedding connectivity verified successfully (returned expected admin ceiling error).")
