import os
import pytest
from app.config import settings
from app.llm import ITIChatClient
from app.rag.embeddings import ITIEmbedder

@pytest.mark.skipif(
    not (
        os.environ.get("RUN_ITI_LIVE_TESTS", "").strip().lower()
        in {"1", "true", "yes"}
        and os.environ.get("ITI_API_KEY")
    ),
    reason="Set RUN_ITI_LIVE_TESTS=1 and ITI_API_KEY to run live ITI tests",
)
def test_iti_chat_live_success():
    """Verify the approved chat model can be invoked through the real gateway."""
    client = ITIChatClient(model="nvidia.nemotron-super-3-120b")

    response = client.invoke([("human", "Reply with exactly OK")], temperature=0, max_tokens=10)

    assert response.response_metadata["model"] == "nvidia.nemotron-super-3-120b"
    assert isinstance(response.content, str)
    assert response.usage_metadata["total_tokens"] > 0

@pytest.mark.skipif(
    not (
        os.environ.get("RUN_ITI_LIVE_TESTS", "").strip().lower()
        in {"1", "true", "yes"}
        and os.environ.get("ITI_API_KEY")
    ),
    reason="Set RUN_ITI_LIVE_TESTS=1 and ITI_API_KEY to run live ITI tests",
)
@pytest.mark.asyncio
async def test_iti_embed_live_success():
    embedder = ITIEmbedder(model="amazon.titan-embed-text-v1", api_key=settings.ITI_API_KEY)

    vectors = await embedder.embed_documents(["User can reset their password."])

    assert len(vectors) == 1
    assert len(vectors[0]) == 1536
