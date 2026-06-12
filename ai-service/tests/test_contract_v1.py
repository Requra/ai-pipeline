import pytest
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import (
    UserStory,
    AcceptanceCriterion,
    ClassifiedRequirement,
    QualityIssue,
    PipelineWarning
)
from app.nodes.format import format_node


@pytest.mark.asyncio
async def test_contract_v1_completed():
    # Setup state representing a successful completed run
    state: PipelineState = {
        "job_id": "test-completed-job",
        "raw_bytes": b"Some text content",
        "file_type": "text",
        "metadata": {"filename": "srs.txt"},
        "raw_text": "Requirements here...",
        "source_metadata": None,
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [
            ClassifiedRequirement(
                id=1,
                text="The system must register users.",
                actor="System",
                goal="register users",
                confidence=0.95,
                evidence=[],
                labels=["FR"]
            )
        ],
        "requirement_coverages": [],
        "user_stories": [
            UserStory(
                id="test-completed-job_story_1",
                title="Register users",
                description="As a System, I want to register users.",
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac1", text="Requirement implemented", criterion_type="plain")
                ],
                source_requirement_ids=[1],
                labels=["FR"]
            )
        ],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.98,
        "status": "success",
        "error": None,
        "started_at": 1000.0,
        "processing_time_ms": 0,
        "functional_requirements": []
    }

    result = await format_node(state)
    jr = result["job_result"]

    # 1. contract_version check
    assert jr.contract_version == "1.0"
    # 2. Status check
    assert jr.status == "completed"
    # 7. List fields check
    assert isinstance(jr.source_documents, list)
    assert len(jr.source_documents) == 1
    assert jr.source_documents[0].file_name == "srs.txt"
    assert jr.source_documents[0].source_type == "text"
    
    # 8. Summary check
    assert jr.summary is not None
    assert hasattr(jr.summary, "executive_summary")
    assert isinstance(jr.summary.key_decisions, list)
    
    # 10. Exports check
    assert jr.exports is not None
    assert jr.exports.excel.available is True
    assert len(jr.exports.excel.rows) == 1
    assert jr.exports.excel.rows[0]["id"] == "US-001"
    assert jr.exports.excel.rows[0]["actor"] == "System"
    
    # 11. Artifacts check
    assert jr.artifacts is not None
    assert jr.artifacts.excel_file.available is False

    # 12. User story structures
    assert len(jr.user_stories) == 1
    us = jr.user_stories[0]
    assert us.id == "US-001"
    assert us.requirement_id == "REQ-001"
    assert us.jira_fields.issue_type == "Story"
    assert us.jira_fields.summary == "Register users"
    assert us.jira_fields.acceptance_criteria == ["Requirement implemented"]
    
    # 13. Traceability/Deduplication/Quality
    assert isinstance(us.source_refs, list)
    assert us.deduplication_key != ""
    assert us.quality.score == 1.0
    
    # Check requirement V1 structure
    assert len(jr.requirements) == 1
    req = jr.requirements[0]
    assert req.id == "REQ-001"
    assert req.type == "Functional"
    assert req.deduplication_key != ""
    assert req.quality.score == 1.0


@pytest.mark.asyncio
async def test_contract_v1_partial_with_error():
    state: PipelineState = {
        "job_id": "test-partial-job",
        "raw_bytes": b"",
        "file_type": "pdf",
        "metadata": {"file_name": "inventory.pdf"},
        "raw_text": "Requirements content",
        "source_metadata": None,
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [
            ClassifiedRequirement(
                id=1,
                text="The system must register users.",
                actor="System",
                goal="register users",
                confidence=0.95,
                evidence=[],
                labels=["FR"]
            )
        ],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [
            QualityIssue(
                item_id=1,
                item_type="requirement",
                severity="medium",
                rule_violated="TEST_RULE",
                details="Missing specific details"
            )
        ],
        "warnings": [
            PipelineWarning(node_name="extract", code="EXTRACT_WARNING", message="Slow extraction")
        ],
        "export_rows": [],
        "summary": None,
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.85,
        "status": "partial",
        "error": "GENERATE_FAILURE: LLM Timeout error\nTraceback (most recent call last):\n  File \"generate.py\", line 123, in node\n    run()",
        "started_at": 1000.0,
        "processing_time_ms": 0,
        "functional_requirements": []
    }

    result = await format_node(state)
    jr = result["job_result"]

    # 4. Partial status check
    assert jr.status == "partial"
    
    # 5. Error check
    assert jr.error is not None
    assert jr.error.node_name == "generate"
    assert jr.error.code == "GENERATE_FAILURE"
    # 6. No stack trace check
    assert jr.error.message == "LLM Timeout error"
    assert jr.error.recoverable is True
    
    # Requirement quality check
    assert len(jr.requirements) == 1
    req = jr.requirements[0]
    assert req.quality.score == 0.85  # 1.0 - 0.15 for quality issue
    assert len(req.quality.issues) == 1
    assert req.quality.issues[0] == "Missing specific details"


@pytest.mark.asyncio
async def test_contract_v1_failed():
    state: PipelineState = {
        "job_id": "test-failed-job",
        "raw_bytes": b"",
        "file_type": "unknown",
        "metadata": {},
        "raw_text": "",
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
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.0,
        "status": "error",
        "error": "EXTRACT_FAILED: No chunks or raw text provided",
        "started_at": 1000.0,
        "processing_time_ms": 0,
        "functional_requirements": []
    }

    result = await format_node(state)
    jr = result["job_result"]

    # 3. No public status "error" anymore - mapped to failed
    assert jr.status == "failed"
    assert jr.error is not None
    assert jr.error.node_name == "extract"
    assert jr.error.code == "EXTRACT_FAILED"
    assert jr.error.message == "No chunks or raw text provided"
    assert jr.error.recoverable is False
