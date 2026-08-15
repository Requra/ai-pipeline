import pytest
from app.nodes.format import format_node
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import (
    ClassifiedRequirement,
    UserStory,
    AcceptanceCriterion,
    EvidenceSpan,
    QualityIssue,
    QualityReportV1,
)


def _make_dummy_requirement(req_id: int = 1, has_evidence: bool = True) -> ClassifiedRequirement:
    evidence = [
        EvidenceSpan(
            chunk_id=f"chk_doc_{req_id}",
            quote="REQ-AUTH-101: Valid user credentials must be verified.",
            page_number=1,
            document_id=f"doc_{req_id}",
            support_score=0.95,
        )
    ] if has_evidence else []
    return ClassifiedRequirement(
        id=req_id,
        text="The system shall authenticate users with valid credentials.",
        actor="User",
        goal="Authenticate into the system",
        candidate_labels=["FR"],
        labels=["FR"],
        confidence=0.95,
        evidence=evidence,
        needs_review=False,
    )


def _make_dummy_story(story_num: int = 1, req_id: int = 1) -> UserStory:
    return UserStory(
        id=f"job_story_{story_num}",
        title="User authentication via credentials",
        description="As a user, I want to authenticate, so that I can access my account.",
        acceptance_criteria=[
            AcceptanceCriterion(
                id=f"job_story_{story_num}_ac_1",
                text="Given valid credentials, when the user logs in, then authentication succeeds.",
                criterion_type="Given-When-Then",
            )
        ],
        source_requirement_ids=[req_id],
        labels=["FR"],
        priority="Medium",
        story_points=3,
        evidence_reference=[
            EvidenceSpan(
                chunk_id=f"chk_doc_{req_id}",
                quote="REQ-AUTH-101: Valid user credentials must be verified.",
                page_number=1,
                document_id=f"doc_{req_id}",
                support_score=0.95,
            )
        ],
    )


@pytest.mark.asyncio
async def test_status_case_a_all_sources_ready_completed():
    """Case A: All sources READY, downstream succeeds, output is clean -> status must be 'completed'."""
    state: PipelineState = {
        "job_id": "test-case-a",
        "is_useful": True,
        "partial_source_failure": False,
        "classified_requirements": [_make_dummy_requirement(1), _make_dummy_requirement(2)],
        "user_stories": [_make_dummy_story(1, 1), _make_dummy_story(2, 2)],
        "warnings": [
            {"node_name": "generate", "code": "GENERATE_STORY_QUALITY", "message": "Minor wording check"},
            {"node_name": "dedupe", "code": "DUPLICATE_REQUIREMENT_MERGED", "message": "Merged dupes"},
        ],
        "quality_issues": [],
        "source_documents": [
            {"document_id": "doc_1", "filename": "requirements.pdf", "file_type": "pdf", "status": "ready"},
            {"document_id": "doc_2", "filename": "meeting.mp3", "file_type": "audio", "status": "ready"},
        ],
    }

    res = await format_node(state)
    assert res["status"] == "completed"
    assert res["job_result"].status == "completed"


@pytest.mark.asyncio
async def test_status_case_b_partial_source_failure_partial():
    """Case B: 1 source failed technically, >=1 source ready -> status must be 'partial'."""
    state: PipelineState = {
        "job_id": "test-case-b",
        "is_useful": True,
        "partial_source_failure": True,
        "classified_requirements": [_make_dummy_requirement(1)],
        "user_stories": [_make_dummy_story(1, 1)],
        "warnings": [
            {"node_name": "prepare_sources", "code": "PARTIAL_SOURCE_FAILURE", "message": "1 of 2 sources failed"},
        ],
        "quality_issues": [],
        "source_documents": [
            {"document_id": "doc_1", "filename": "corrupt.pdf", "file_type": "pdf", "status": "failed"},
            {"document_id": "doc_2", "filename": "meeting.mp3", "file_type": "audio", "status": "ready"},
        ],
    }

    res = await format_node(state)
    assert res["status"] == "partial"
    assert res["job_result"].status == "partial"


@pytest.mark.asyncio
async def test_status_case_c_irrelevant_source_with_ready_source_completed():
    """Case C: 1 source rejected as irrelevant, other source usable -> status must be 'completed' with warning."""
    state: PipelineState = {
        "job_id": "test-case-c",
        "is_useful": True,
        "partial_source_failure": False,
        "classified_requirements": [_make_dummy_requirement(1)],
        "user_stories": [_make_dummy_story(1, 1)],
        "warnings": [
            {"node_name": "prepare_sources", "code": "SOURCE_REJECTED_IRRELEVANT", "message": "recipe.txt was rejected"},
        ],
        "quality_issues": [],
        "source_documents": [
            {"document_id": "doc_1", "filename": "recipe.txt", "file_type": "text", "status": "rejected"},
            {"document_id": "doc_2", "filename": "requirements.pdf", "file_type": "pdf", "status": "ready"},
        ],
    }

    res = await format_node(state)
    assert res["status"] == "completed"
    assert res["job_result"].status == "completed"


@pytest.mark.asyncio
async def test_status_case_d_all_sources_failed_technically():
    """Case D: All sources fail technically -> status must be 'failed'."""
    state: PipelineState = {
        "job_id": "test-case-d",
        "is_useful": False,
        "error": "ALL_SOURCES_FAILED: All 2 source(s) failed during preparation.",
        "classified_requirements": [],
        "user_stories": [],
        "source_documents": [
            {"document_id": "doc_1", "filename": "bad1.pdf", "file_type": "pdf", "status": "failed"},
            {"document_id": "doc_2", "filename": "bad2.docx", "file_type": "docx", "status": "failed"},
        ],
    }

    res = await format_node(state)
    assert res["status"] == "failed"
    assert res["job_result"].status == "failed"


@pytest.mark.asyncio
async def test_status_case_e_all_sources_rejected_irrelevant():
    """Case E: All sources rejected as irrelevant -> status must be 'rejected'."""
    state: PipelineState = {
        "job_id": "test-case-e",
        "is_useful": False,
        "error": "DOCUMENT_REJECTED: All source(s) rejected as irrelevant to software delivery.",
        "classified_requirements": [],
        "user_stories": [],
        "source_documents": [
            {"document_id": "doc_1", "filename": "pizza_recipe.txt", "file_type": "text", "status": "rejected"},
        ],
    }

    res = await format_node(state)
    assert res["status"] == "rejected"
    assert res["job_result"].status == "rejected"


@pytest.mark.asyncio
async def test_status_case_f_fatal_quality_issue_results_in_partial():
    """Case F: High severity ungrounded requirement without evidence -> status must be 'partial'."""
    state: PipelineState = {
        "job_id": "test-case-f",
        "is_useful": True,
        "partial_source_failure": False,
        "classified_requirements": [_make_dummy_requirement(1, has_evidence=False)],
        "user_stories": [_make_dummy_story(1, 1)],
        "quality_issues": [
            QualityIssue(
                item_id=1,
                item_type="requirement",
                severity="high",
                rule_violated="missing_evidence",
                details="Requirement missing evidence",
            )
        ],
    }

    res = await format_node(state)
    assert res["status"] == "partial"
    assert res["job_result"].status == "partial"
