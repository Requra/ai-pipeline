import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.graph.pipeline import build_pipeline
from app.nodes.extract import ExtractionResponse, ExtractedRequirement, EvidenceSpan
from app.nodes.classify import RequirementClassification, ClassificationResponse
from app.nodes.generate import GenerationResponse, StoryResponse
from app.schemas.items import JobResult


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
         patch("langchain_core.prompts.ChatPromptTemplate.from_messages", side_effect=lambda m: FakePrompt(m)):

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
         patch("langchain_core.prompts.ChatPromptTemplate.from_messages", side_effect=lambda m: FakePrompt(m)):

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
