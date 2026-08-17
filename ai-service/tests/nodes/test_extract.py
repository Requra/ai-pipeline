import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from app.nodes.extract import extract_node, ExtractionResponse, preprocess_text, align_quote_to_source
from app.schemas.items import SourceChunk, ExtractedRequirement, EvidenceSpan

def test_preprocess_text_acronym_safety():
    """
    Ensure technical acronyms like ER and AH are preserved while fillers are removed.
    """
    # 1. Acronyms should be preserved
    assert "ER diagram" in preprocess_text("ER diagram")
    assert "AH header" in preprocess_text("AH header")
    
    # 2. Lowercase fillers should be removed
    text_with_fillers = "The system uh should um handle AH header."
    cleaned = preprocess_text(text_with_fillers)
    assert "uh" not in cleaned
    assert "um" not in cleaned
    assert "AH header" in cleaned

def test_align_quote_to_source():
    """
    Verify the quote alignment logic.
    """
    original = "The system shall process payments. Performance must be under 2s."
    
    # 1. Exact match
    assert align_quote_to_source("process payments", original) == "process payments"
    
    # 2. Fuzzy match (normalized)
    # LLM might return slightly cleaned text
    fuzzy_quote = "performance MUST BE under 2s"
    aligned = align_quote_to_source(fuzzy_quote, original)
    assert aligned == "Performance must be under 2s"
    assert aligned in original
    
    # 3. Fallback (no match found)
    bad_quote = "This is not in the text at all."
    fallback = align_quote_to_source(bad_quote, original)
    assert fallback in original
    assert len(fallback) > 0

@pytest.fixture
def mock_extraction_json():
    return json.dumps({
        "requirements": [
            {
                "id": 1,
                "text": "The system shall process payments.",
                "actor": "System",
                "goal": "process payments",
                "candidate_labels": ["FR"],
                "confidence": 0.9,
                "evidence": [{"chunk_id": "c1", "quote": "process payments"}]
            },
            {
                "id": 2,
                "text": "Performance must be under 2s.",
                "actor": "System",
                "goal": "performance",
                "candidate_labels": ["NFR"],
                "confidence": 0.85,
                "evidence": [{"chunk_id": "c1", "quote": "under 2s"}]
            }
        ]
    })

@pytest.mark.asyncio
async def test_extract_node_evidence_alignment(base_state):
    """
    PROVE every EvidenceSpan.quote is found in the original chunk.text.
    """
    state = base_state.copy()
    original_text = "The system shall process payments. Performance must be under 2s."
    state["chunks"] = [
        SourceChunk(chunk_id="chunk_1", text=original_text, start_char=0, end_char=len(original_text))
    ]
    
    # Mock LLM returning a quote that is slightly different from source (e.g. from cleaned text)
    mock_resp_content = json.dumps({
        "requirements": [
            {
                "id": 1,
                "text": "Requirement 1",
                "candidate_labels": ["FR"],
                "confidence": 1.0,
                "evidence": [{"chunk_id": "chunk_1", "quote": "process PAYMENTS"}]
            }
        ]
    })
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp_content))
    
    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    assert result["status"] == "success"
    req = result["extracted_requirements"][0]
    quote = req.evidence[0].quote
    
    # MUST exist in original text
    assert quote in original_text
    assert quote == "process payments" # Corrected from "process PAYMENTS"
    assert req.needs_review is True
    assert "AUTO_FIX" in req.review_reason

@pytest.mark.asyncio
async def test_extract_node_with_raw_text(base_state, mock_extraction_json):
    state = base_state.copy()
    state["raw_text"] = "The system shall process payments. Performance must be under 2s."
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_extraction_json))
    
    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    assert result["status"] == "success"
    assert "extracted_requirements" in result
    assert "functional_requirements" in result
    
    assert len(result["extracted_requirements"]) == 2
    assert len(result["functional_requirements"]) == 1 # Only FR should be projected
    assert result["extracted_requirements"][0].candidate_labels == ["FR"]
    assert result["extracted_requirements"][1].candidate_labels == ["NFR"]
    assert result["extracted_requirements"][0].evidence[0].chunk_id == "raw_fallback"

@pytest.mark.asyncio
async def test_extract_node_with_chunks(base_state):
    state = base_state.copy()
    state["chunks"] = [
        SourceChunk(chunk_id="chunk_1", text="The system shall process payments.", start_char=0, end_char=30),
        SourceChunk(chunk_id="chunk_2", text="Performance must be under 2s.", start_char=31, end_char=60)
    ]
    
    mock_resp_1 = json.dumps({
        "requirements": [{
            "id": 1,
            "text": "The system shall process payments.",
            "candidate_labels": ["FR"],
            "confidence": 1.0,
            "evidence": [{"quote": "process payments", "chunk_id": "chunk_1"}]
        }]
    })
    mock_resp_2 = json.dumps({
        "requirements": [{
            "id": 1,
            "text": "Performance must be under 2s.",
            "candidate_labels": ["NFR"],
            "confidence": 1.0,
            "evidence": [{"quote": "under 2s", "chunk_id": "chunk_2"}]
        }]
    })


    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock()
    mock_llm.ainvoke.side_effect = [
        MagicMock(content=mock_resp_1),
        MagicMock(content=mock_resp_2)
    ]
    
    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    assert result["status"] == "success"
    assert len(result["extracted_requirements"]) == 2
    # Verify chunk_id enrichment
    assert result["extracted_requirements"][0].evidence[0].chunk_id == "chunk_1"
    assert result["extracted_requirements"][1].evidence[0].chunk_id == "chunk_2"

@pytest.mark.asyncio
async def test_extract_node_empty_failure(base_state):
    state = base_state.copy()
    state["raw_text"] = "Some random text with no requirements."
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="{\"requirements\": []}"))
    
    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    assert result["status"] == "partial"
    assert result["extracted_requirements"] == []
    assert any(w["code"] == "EXTRACT_EMPTY" for w in result.get("warnings", []))

@pytest.mark.asyncio
async def test_extract_node_llm_failure(base_state):
    state = base_state.copy()
    state["raw_text"] = "Error inducing text."
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("API Error"))
    
    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    assert result["status"] == "error"
    assert result["extracted_requirements"] == []
    assert result["functional_requirements"] == []
    assert result.get("error_code") == "EXTRACT_PROVIDER_FAILURE"
