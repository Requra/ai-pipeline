import pytest
from app.nodes.ingest import ingest_node

@pytest.mark.asyncio
async def test_ingest_pdf_real(base_state, sample_pdf_bytes):
    state = base_state.copy()
    state["raw_bytes"] = sample_pdf_bytes
    state["file_type"] = "pdf"
    
    result = await ingest_node(state)
    
    assert "raw_text" in result
    assert "allow users to login" in result["raw_text"].lower()
    assert result["is_useful"] is True
    assert result["relevance_score"] > 0.5

@pytest.mark.asyncio
async def test_ingest_docx_real(base_state, sample_docx_bytes):
    state = base_state.copy()
    state["raw_bytes"] = sample_docx_bytes
    state["file_type"] = "docx"
    
    result = await ingest_node(state)
    
    assert "raw_text" in result
    assert "reset my password" in result["raw_text"].lower()
    assert result["is_useful"] is True

@pytest.mark.asyncio
async def test_ingest_invalid_file(base_state):
    state = base_state.copy()
    state["raw_bytes"] = b"not a pdf"
    state["file_type"] = "pdf"
    
    result = await ingest_node(state)
    
    # It should either error or reject
    assert "error" in result or result.get("is_useful") is False
