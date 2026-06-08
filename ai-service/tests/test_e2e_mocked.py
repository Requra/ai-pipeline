import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.graph.pipeline import build_pipeline
from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.nodes.extract import ExtractionResponse, ExtractedRequirement, EvidenceSpan
from app.nodes.classify import RequirementClassification, ClassificationResponse
from app.nodes.generate import GenerationResponse, StoryResponse
from app.schemas.items import JobResult
from app.nodes.ingest import RelevanceCheck


class FakePrompt:
    def __init__(self, messages):
        self.messages = messages

    def __or__(self, other):
        # return a fake chain whose ainvoke will produce deterministic outputs
        async def ainvoke(payload):
            system = "".join(m[1] for m in self.messages if isinstance(m, tuple))
            # Extract node
            if "Extract requirements" in system or "Extract" in system:
                # Return two extracted requirements
                r1 = ExtractedRequirement(
                    id=1,
                    text="The system shall process payments.",
                    actor="System",
                    goal="process payments",
                    candidate_labels=["FR"],
                    confidence=0.9,
                    evidence=[EvidenceSpan(chunk_id="c1", quote="process payments")]
                )
                r2 = ExtractedRequirement(
                    id=2,
                    text="Performance must be under 2s.",
                    actor="System",
                    goal="performance",
                    candidate_labels=["NFR"],
                    confidence=0.8,
                    evidence=[EvidenceSpan(chunk_id="c1", quote="under 2s")]
                )
                return ExtractionResponse(requirements=[r1, r2])

            # Classify node
            if "requirements analyst" in system:
                c1 = RequirementClassification(id=1, labels=["FR"], confidence=0.9)
                c2 = RequirementClassification(id=2, labels=["NFR"], confidence=0.8)
                return ClassificationResponse(classifications=[c1, c2])

            # Generate node
            if "Convert requirements into USER STORIES" in system:
                s1 = StoryResponse(id=1, title="Process payments", description="As a System, ...", acceptance_criteria=["Given X"], labels=["FR"]) 
                s2 = StoryResponse(id=2, title="Improve perf", description="As a System, ...", acceptance_criteria=["Given Y"], labels=["NFR"]) 
                return GenerationResponse(stories=[s1, s2])

            # Summarize node
            if "expert business analyst" in system:
                class Resp:
                    content = "Executive summary: important points"
                return Resp()

            # Default
            return None

        chain = MagicMock()
        chain.ainvoke = AsyncMock(side_effect=ainvoke)
        return chain


@pytest.mark.asyncio
async def test_process_json_end_to_end_mocked():
    pipeline = build_pipeline()

    # Fake LLM and prompt plumbing
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = MagicMock()
    with patch("app.llm.get_llm", return_value=mock_llm), \
         patch("app.nodes.extract.get_llm", return_value=mock_llm), \
         patch("app.nodes.classify.get_llm", return_value=mock_llm), \
         patch("app.nodes.generate.get_llm", return_value=mock_llm), \
         patch("app.nodes.summarize.get_llm", return_value=mock_llm), \
         patch("langchain_core.prompts.ChatPromptTemplate.from_messages", side_effect=lambda m: FakePrompt(m)), \
         patch("app.nodes.ingest._run_relevance_check", new=AsyncMock(return_value=RelevanceCheck(is_useful=True, relevance_score=1.0, reason="test mocked"))):

        initial_state = {
            "job_id": "e2e-json-1",
            "raw_bytes": b"",
            "raw_text": "The system shall process payments. Performance must be under 2s.",
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

        # Pipeline should attach job_result with status
        assert "job_result" in result
        jr = result["job_result"]
        assert isinstance(jr, JobResult)
        assert jr.status in ("success", "partial", "rejected", "error", "needs_review")
        # Ensure the NFR/FR mapping preserved in outputs
        reqs = jr.requirements
        assert any(r.id == 1 for r in reqs)
        assert any(r.id == 2 for r in reqs)
        # Stories produced
        assert len(jr.user_stories) == 2


@pytest.mark.asyncio
async def test_pdf_end_to_end_mocked():
    pipeline = build_pipeline()

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = MagicMock()
    with patch("app.llm.get_llm", return_value=mock_llm), \
         patch("app.nodes.extract.get_llm", return_value=mock_llm), \
         patch("app.nodes.classify.get_llm", return_value=mock_llm), \
         patch("app.nodes.generate.get_llm", return_value=mock_llm), \
         patch("app.nodes.summarize.get_llm", return_value=mock_llm), \
         patch("langchain_core.prompts.ChatPromptTemplate.from_messages", side_effect=lambda m: FakePrompt(m)), \
         patch("app.nodes.ingest._run_relevance_check", new=AsyncMock(return_value=RelevanceCheck(is_useful=True, relevance_score=1.0, reason="test mocked"))):

        initial_state = {
            "job_id": "e2e-pdf-1",
            "raw_bytes": b"%PDF-1.4 test",
            "raw_text": "The system shall process payments. Performance must be under 2s.",
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

        assert "job_result" in result
        jr = result["job_result"]
        assert isinstance(jr, JobResult)
        assert len(jr.user_stories) == 2
        # Ensure requirement ids are unique
        ids = [r.id for r in jr.requirements]
        assert len(ids) == len(set(ids))


def test_api_process_json_returns_job_result(monkeypatch):
    # Reuse the same FakePrompt behavior by patching get_llm and prompt builder
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = MagicMock()

    monkeypatch.setattr("app.llm.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.extract.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.classify.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.generate.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("app.nodes.summarize.get_llm", lambda *a, **k: mock_llm)
    monkeypatch.setattr("langchain_core.prompts.ChatPromptTemplate.from_messages", lambda m: FakePrompt(m))

    client = TestClient(fastapi_app)
    payload = {
        "job_id": "api-json-1",
        "text": "The system shall process payments.",
        "file_type": "text",
        "metadata": {}
    }

    resp = client.post("/process-json", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    # Should be the JobResult model fields (job_id and status present)
    assert body.get("job_id") == "api-json-1"
    assert "status" in body
    # Ensure internal raw_bytes not returned
    assert "raw_bytes" not in body
