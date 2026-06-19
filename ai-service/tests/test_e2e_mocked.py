import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.graph.pipeline import build_pipeline
from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.schemas.items import JobResult
from app.nodes.ingest import RelevanceCheck

async def mocked_ainvoke(messages, **kwargs):
    # The first message is system prompt
    system = messages[0][1]
    
    # Ingest node relevance check
    if "strict software-document gatekeeper" in system:
        return MagicMock(content=json.dumps({
            "is_useful": True,
            "relevance_score": 1.0,
            "reason": "test mocked"
        }))
    
    # Extract node
    if "Extract atomic software requirements" in system or "Extract requirements" in system:
        return MagicMock(content=json.dumps({
            "requirements": [
                {
                    "id": 1,
                    "text": "The system shall process payments and ensure that all transactions are secure and encrypted. Performance must be under 2 seconds.",
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
                    "confidence": 0.8,
                    "evidence": [{"chunk_id": "c1", "quote": "under 2s"}]
                }
            ]
        }))
        
    # Classify node
    if "You classify each requirement" in system:
        return MagicMock(content=json.dumps({
            "classifications": [
                {"id": 1, "labels": ["FR"], "confidence": 0.9},
                {"id": 2, "labels": ["NFR"], "confidence": 0.8}
            ]
        }))
        
    # Generate node
    if "Convert requirements into USER STORIES" in system:
        return MagicMock(content=json.dumps({
            "stories": [
                {"source_requirement_id": 1, "title": "Process payments", "description": "As a System, ...", "acceptance_criteria": ["Given X"], "labels": ["FR"]},
                {"source_requirement_id": 2, "title": "Improve perf", "description": "As a System, ...", "acceptance_criteria": ["Given Y"], "labels": ["NFR"]}
            ]
        }))
        
    # Summarize node
    if "expert business analyst" in system:
        return MagicMock(content="Executive summary: important points")
        
    return MagicMock(content="{}")

@pytest.mark.asyncio
async def test_process_json_end_to_end_mocked():
    pipeline = build_pipeline()

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=mocked_ainvoke)
    
    with patch("app.llm.get_llm", return_value=mock_llm), \
         patch("app.nodes.ingest.get_llm", return_value=mock_llm), \
         patch("app.nodes.extract.get_llm", return_value=mock_llm), \
         patch("app.nodes.classify.get_llm", return_value=mock_llm), \
         patch("app.nodes.generate.get_llm", return_value=mock_llm), \
         patch("app.nodes.summarize.get_llm", return_value=mock_llm):

        initial_state = {
            "job_id": "e2e-json-1",
            "raw_bytes": b"",
            "raw_text": "The system shall process payments and ensure that all transactions are secure and encrypted. Performance must be under 2 seconds.",
            "file_type": "text",
            "metadata": {},
            "source_metadata": None,
            "chunks": [],
            "extracted_requirements": [],
            "classified_requirements": [],
            "requirement_coverages": [],
            "user_stories": [],
            "quality_issues": [],
            "warnings": [],
            "export_rows": [],
            "summary": None,
            "is_useful": True,
            "relevance_score": 1.0,
            "status": "started",
            "error": None,
            "started_at": 0,
            "processing_time_ms": 0,
            "functional_requirements": []
        }

        result = await pipeline.ainvoke(initial_state)

        assert "job_result" in result
        jr = result["job_result"]
        assert isinstance(jr, JobResult)
        assert jr.status == "completed"
        assert len(jr.requirements) == 2
        assert len(jr.user_stories) == 2


@pytest.mark.asyncio
async def test_pdf_end_to_end_mocked():
    pipeline = build_pipeline()

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=mocked_ainvoke)
    
    with patch("app.llm.get_llm", return_value=mock_llm), \
         patch("app.nodes.ingest.get_llm", return_value=mock_llm), \
         patch("app.nodes.extract.get_llm", return_value=mock_llm), \
         patch("app.nodes.classify.get_llm", return_value=mock_llm), \
         patch("app.nodes.generate.get_llm", return_value=mock_llm), \
         patch("app.nodes.summarize.get_llm", return_value=mock_llm):

        initial_state = {
            "job_id": "e2e-pdf-1",
            "raw_bytes": b"%PDF-1.4 test",
            "raw_text": "The system shall process payments and ensure that all transactions are secure and encrypted. Performance must be under 2 seconds.",
            "file_type": "pdf",
            "metadata": {"filename": "sample.pdf"},
            "source_metadata": None,
            "chunks": [],
            "extracted_requirements": [],
            "classified_requirements": [],
            "requirement_coverages": [],
            "user_stories": [],
            "quality_issues": [],
            "warnings": [],
            "export_rows": [],
            "summary": None,
            "is_useful": True,
            "relevance_score": 1.0,
            "status": "started",
            "error": None,
            "started_at": 0,
            "processing_time_ms": 0,
            "functional_requirements": []
        }

        result = await pipeline.ainvoke(initial_state)

        jr = result["job_result"]
        if jr.status == "error":
            print(f"PIPELINE ERROR: {jr.error_message}")
        assert jr.status == "completed"
        assert len(jr.user_stories) == 2


def test_api_process_json_returns_job_result(monkeypatch):
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=mocked_ainvoke)

    monkeypatch.setattr("app.llm.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.ingest.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.extract.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.classify.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.generate.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.summarize.get_llm", lambda *a, **k: mock_llm)

    client = TestClient(fastapi_app)
    payload = {
        "job_id": "api-json-1",
        "content": "The system shall process payments and ensure that all transactions are secure and encrypted. Performance must be under 2 seconds.",
        "source_type": "text",
        "metadata": {}
    }

    resp = client.post("/process-json", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body.get("job_id") == "api-json-1"
    assert body.get("status") == "QUEUED"

    # Verify background task execution via the polling status endpoint
    status_resp = client.get("/status/api-json-1")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body.get("status") == "COMPLETED"
    assert "result" in status_body
