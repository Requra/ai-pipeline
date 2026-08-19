import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.schemas.items import SourceChunk, ExtractedRequirement, EvidenceSpan
from app.schemas.pipeline_state import PipelineState
from app.services.source_processing.models import SourceInput
from app.services.source_processing.document import process_document_source
from app.nodes.extract import (
    normalize_extraction_payload,
    process_chunk,
    extract_node,
    ChunkExtractionOutcome,
)
from app.services.quality_scoring import compute_quality_scores


@pytest.fixture
def dummy_chunk():
    return SourceChunk(
        chunk_id="chk_test_1",
        text="The system shall enforce password complexity with minimum 8 characters.",
        start_char=0,
        end_char=70,
        page_number=1,
    )


# ---------------------------------------------------------------------------
# 1. DOCX Modality & Dispatch Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_docx_modality_dispatch_when_file_type_is_document():
    """Verify that file_type='document' with docx filename/bytes is processed as DOCX."""
    from pathlib import Path
    docx_path = Path("test-fixtures/e2e_real_mixed/technical-notes.docx")
    if not docx_path.exists():
        pytest.skip("Fixture not found")

    raw_bytes = docx_path.read_bytes()
    src = SourceInput(
        document_id="doc_docx_001",
        filename="technical-notes.docx",
        file_type="document",  # Generic 'document' type from API / multipart
        raw_bytes=raw_bytes,
    )

    processed = await process_document_source(src, "job-test-docx")
    assert processed.status == "ready"
    assert processed.is_useful is True
    assert processed.source_type == "docx"
    assert "DOC-TECH-002" in processed.raw_text
    assert "password reset attempts must be recorded" in processed.raw_text
    assert len(processed.chunks) >= 1
    assert processed.docx_paragraphs is not None
    assert len(processed.docx_paragraphs) > 0


# ---------------------------------------------------------------------------
# 2. Normalization Payload Hardening Tests
# ---------------------------------------------------------------------------

def test_normalize_single_requirement_object(dummy_chunk):
    """Test when LLM returns a single dict rather than a list."""
    payload = {
        "id": 1,
        "text": "All reset attempts must be logged.",
        "candidate_labels": ["FR", "Constraint"],
        "disposition": "accepted",
    }
    res = normalize_extraction_payload(payload, dummy_chunk)
    assert len(res["requirements"]) == 1
    req = res["requirements"][0]
    assert req["text"] == "All reset attempts must be logged."
    assert "FR" in req["candidate_labels"]
    assert req["evidence"][0]["chunk_id"] == "chk_test_1"


def test_normalize_dict_with_numeric_string_keys(dummy_chunk):
    """Test when LLM returns a map of numeric keys {'1': {...}, '2': {...}}."""
    payload = {
        "1": {"text": "Req 1", "candidate_labels": ["FR"]},
        "2": {"text": "Req 2", "candidate_labels": ["NFR"]},
    }
    res = normalize_extraction_payload(payload, dummy_chunk)
    assert len(res["requirements"]) == 2
    assert res["requirements"][0]["text"] == "Req 1"
    assert res["requirements"][1]["text"] == "Req 2"


def test_normalize_shorthand_multiple_keys(dummy_chunk):
    """Test when LLM returns {'FR': '...', 'NFR': '...'}."""
    payload = {
        "FR": "User must receive SMS code.",
        "NFR": "Response time shall be under 200ms.",
    }
    res = normalize_extraction_payload(payload, dummy_chunk)
    assert len(res["requirements"]) == 2
    labels = [r["candidate_labels"][0] for r in res["requirements"]]
    assert "FR" in labels
    assert "NFR" in labels


def test_normalize_alternate_wrappers(dummy_chunk):
    """Test when LLM wraps list under 'data' or 'results'."""
    for wrapper in ["data", "results", "extracted_requirements", "payload"]:
        payload = {wrapper: [{"text": f"Requirement from {wrapper}", "candidate_labels": ["FR"]}]}
        res = normalize_extraction_payload(payload, dummy_chunk)
        assert len(res["requirements"]) == 1
        assert res["requirements"][0]["text"] == f"Requirement from {wrapper}"


# ---------------------------------------------------------------------------
# 3. Extraction Error Isolation & Telemetry Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_chunk_model_timeout_classification(dummy_chunk):
    """Verify TimeoutError produces MODEL_TIMEOUT outcome without crashing."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError("Call timed out"))

    outcome = await process_chunk(mock_llm, dummy_chunk)
    assert isinstance(outcome, ChunkExtractionOutcome)
    assert outcome.outcome_type == "MODEL_TIMEOUT"
    assert outcome.error_code == "EXTRACT_MODEL_TIMEOUT"
    assert len(outcome) == 0


@pytest.mark.asyncio
async def test_process_chunk_model_provider_error_classification(dummy_chunk):
    """Verify provider 429/500/404 produces MODEL_FAILURE outcome."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("Provider 429 Rate Limit Exceeded"))

    outcome = await process_chunk(mock_llm, dummy_chunk)
    assert outcome.outcome_type == "MODEL_FAILURE"
    assert outcome.error_code == "EXTRACT_PROVIDER_FAILURE"
    assert "Rate Limit" in (outcome.error_message or "")


@pytest.mark.asyncio
async def test_extract_node_all_technical_failures_reports_error_not_empty(dummy_chunk):
    """Verify that when all chunks fail technically, extract_node reports 'error' with technical code."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("Provider Connection Refused"))

    state = {
        "job_id": "job-test-tech-fail",
        "chunks": [dummy_chunk],
        "is_useful": True,
    }

    from unittest.mock import patch
    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    assert result["status"] == "error"
    assert result["error_code"] == "EXTRACT_PROVIDER_FAILURE"
    assert "EXTRACT_PROVIDER_FAILURE" in result["error"]
    assert result["extraction_telemetry"]["chunks_model_failed"] == 1
    assert result["extraction_telemetry"]["chunks_total"] == 1


# ---------------------------------------------------------------------------
# 4. Honest Quality Scoring Tests for Zero Extraction
# ---------------------------------------------------------------------------

def test_quality_scoring_zero_requirements_zero_scores():
    """Verify that when requirement_count == 0, submetrics are 0.0 rather than vacuous 1.0."""
    from app.schemas.items import QualityIssue
    issues = [
        QualityIssue(
            item_id=0,
            item_type="requirement",
            severity="high",
            rule_violated="USEFUL_INPUT_WITH_EMPTY_EXTRACTION",
            details="Document was accepted as useful but no requirements were extracted."
        )
    ]
    scores = compute_quality_scores([], [], issues)
    assert scores.requirement_count == 0
    assert scores.story_count == 0
    assert scores.groundedness_score == 0.0
    assert scores.traceability_coverage == 0.0
    assert scores.story_completeness == 0.0
    assert scores.acceptance_criteria_quality == 0.0
    assert scores.overall_score == 0.0
    assert scores.high_severity_issue_count == 1
