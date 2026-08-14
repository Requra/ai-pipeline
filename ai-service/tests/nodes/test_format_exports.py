"""Phase 8 — story-type mapping + enriched export rows."""

from __future__ import annotations

import pytest

from app.nodes.format import format_node, v1_type_from_labels
from app.schemas.items import (
    AcceptanceCriterion,
    ClassifiedRequirement,
    EvidenceSpan,
    PipelineWarning,
    QualityIssue,
    UserStory,
)


def test_v1_type_from_labels():
    assert v1_type_from_labels(["FR"]) == "Functional"
    assert v1_type_from_labels(["NFR"]) == "Non-Functional"
    assert v1_type_from_labels(["Constraint"]) == "Non-Functional"
    assert v1_type_from_labels(["BR"]) == "Business"
    assert v1_type_from_labels([]) == "Functional"
    assert v1_type_from_labels(["FR", "NFR"]) == "Non-Functional"


def _req(rid, labels, conf=0.9):
    return ClassifiedRequirement(
        id=rid, text=f"Requirement {rid}", actor="user", goal="do thing",
        candidate_labels=labels, labels=labels, confidence=conf, classification_confidence=conf,
        evidence=[EvidenceSpan(chunk_id=f"c{rid}", quote=f"quote for {rid}", support_score=1.0)],
    )


def _story(sid, rid, labels):
    return UserStory(
        id=sid, title=f"Story {rid}",
        description="As a user, I want X, so that Y.",
        acceptance_criteria=[
            AcceptanceCriterion(id=f"{sid}_ac_1", text="Given A, when B, then C clearly.", criterion_type="Given-When-Then"),
            AcceptanceCriterion(id=f"{sid}_ac_2", text="Given D, when E, then F with error.", criterion_type="Given-When-Then"),
        ],
        source_requirement_ids=[rid], labels=labels,
        evidence_reference=[EvidenceSpan(chunk_id=f"c{rid}", quote=f"quote for {rid}")],
    )


@pytest.mark.asyncio
async def test_story_type_maps_from_labels(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [_req(1, ["FR"]), _req(2, ["NFR"]), _req(3, ["BR"])]
    state["user_stories"] = [_story("s1", 1, ["FR"]), _story("s2", 2, ["NFR"]), _story("s3", 3, ["BR"])]

    result = await format_node(state)
    jr = result["job_result"]
    types = {s.title: s.type for s in jr.user_stories}
    assert types["Story 1"] == "Functional"
    assert types["Story 2"] == "Non-Functional"
    assert types["Story 3"] == "Business"


@pytest.mark.asyncio
async def test_export_rows_are_enriched(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [_req(1, ["FR"], conf=0.77)]
    state["user_stories"] = [_story("s1", 1, ["FR"])]

    result = await format_node(state)
    jr = result["job_result"]

    assert jr.exports.excel.available is True
    assert jr.exports.jira.available is True
    row = jr.exports.excel.rows[0]
    # Enriched, useful columns are present and populated.
    assert row["requirement_id"].startswith("REQ-")
    assert row["confidence"] == 0.931
    assert "Given A" in row["acceptance_criteria"]
    assert "quote for 1" in row["source_quotes"]
    assert row["type"] == "Functional"
    # Jira rows carry acceptance criteria + traceability.
    jira = jr.exports.jira.rows[0]
    assert len(jira["acceptance_criteria"]) == 2
    assert "quote for 1" in jira["source_quotes"]


@pytest.mark.asyncio
async def test_requirement_confidence_is_calibrated_and_unsupported_goal_is_not_title(base_state):
    requirement = ClassifiedRequirement(
        id=1,
        text="Checkout requests shall undergo manager approval above $1,000.",
        actor="Manager",
        goal="Approve or reject checkout requests",
        candidate_labels=["BR"], labels=["BR"],
        confidence=0.7, classification_confidence=0.9,
        evidence=[EvidenceSpan(
            chunk_id="c1",
            quote="Checkout requests shall undergo manager approval above $1,000.",
            support_score=1.0,
        )],
    )
    state = base_state.copy()
    state["classified_requirements"] = [requirement]
    state["user_stories"] = [_story("s1", 1, ["BR"])]

    result = await format_node(state)
    public_requirement = result["job_result"].requirements[0]

    assert public_requirement.confidence_score == 0.91
    assert "reject" not in public_requirement.title.lower()
    assert "manager approval" in public_requirement.title.lower()


@pytest.mark.asyncio
async def test_audio_source_reference_preserves_uploaded_document_identity(base_state):
    state = base_state.copy()
    state.update({
        "file_type": "audio",
        "metadata": {"filename": "fallback-name.mp3"},
        "source_documents": [{
            "document_id": "audio-source-1",
            "filename": "requirements-meeting.mp3",
            "file_type": "audio",
            "mime_type": "audio/mpeg",
        }],
    })
    requirement = ClassifiedRequirement(
        id=1, text="The system shall use TLS 1.3.", actor="System", goal="Use TLS 1.3",
        candidate_labels=["NFR"], labels=["NFR"], confidence=0.8,
        classification_confidence=0.8,
        evidence=[EvidenceSpan(
            chunk_id="trans_job_semantic_0", quote="The system shall use TLS 1.3.",
            document_id="audio-source-1", support_score=0.9,
        )],
    )
    state["classified_requirements"] = [requirement]
    state["user_stories"] = [_story("s1", 1, ["NFR"])]

    result = await format_node(state)

    ref = result["job_result"].requirements[0].source_refs[0]
    assert ref.source_type == "audio"
    assert ref.source_id == "audio-source-1"
    assert ref.document_name == "requirements-meeting.mp3"
    assert ref.page is None


@pytest.mark.asyncio
async def test_no_fake_artifact_url(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [_req(1, ["FR"])]
    state["user_stories"] = [_story("s1", 1, ["FR"])]
    result = await format_node(state)
    jr = result["job_result"]
    # XLSX binary is produced by the backend, not faked here.
    assert jr.artifacts.excel_file.available is False
    assert jr.artifacts.excel_file.file_url == ""


@pytest.mark.asyncio
async def test_exports_empty_when_no_stories(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [_req(1, ["FR"])]
    state["user_stories"] = []
    result = await format_node(state)
    jr = result["job_result"]
    assert jr.exports.excel.available is False
    assert jr.exports.jira.available is False


@pytest.mark.asyncio
async def test_format_deduplicates_identical_document_source_refs(base_state):
    requirement = _req(1, ["FR"])
    requirement.evidence = [
        EvidenceSpan(
            chunk_id="c1",
            document_id="doc-1",
            quote="The system shall export a report.",
            support_score=0.70,
        ),
        EvidenceSpan(
            chunk_id="c2",
            document_id="doc-1",
            quote="The system shall export a report.",
            support_score=1.0,
        ),
    ]
    story = _story("s1", 1, ["FR"])
    story.evidence_reference = list(requirement.evidence)
    state = base_state.copy()
    state["classified_requirements"] = [requirement]
    state["user_stories"] = [story]
    state["source_documents"] = [
        {
            "document_id": "doc-1",
            "filename": "requirements.pdf",
            "file_type": "pdf",
        }
    ]

    result = await format_node(state)
    job = result["job_result"]

    assert len(job.requirements[0].source_refs) == 1
    assert job.requirements[0].source_refs[0].confidence_score == 1.0
    assert len(job.user_stories[0].source_refs) == 1


@pytest.mark.asyncio
async def test_diagnostic_warnings_do_not_force_partial_status(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [_req(1, ["FR"])]
    state["user_stories"] = [_story("s1", 1, ["FR"])]
    state["quality_issues"] = [
        QualityIssue(
            item_id=1,
            item_type="requirement",
            severity="medium",
            rule_violated="semantic_conflict_complementary",
            details="The requirements are complementary.",
        )
    ]
    state["warnings"] = [
        PipelineWarning(
            node_name="extract",
            code="EXTRACT_WEAK_EVIDENCE",
            message="Fallback evidence was later grounded.",
        ),
        PipelineWarning(
            node_name="dedupe_requirements",
            code="SEMANTIC_COMPLEMENTARY",
            message="Informational relationship.",
        ),
        PipelineWarning(
            node_name="retrieve_evidence",
            code="NO_RETRIEVED_EVIDENCE",
            message="Existing extracted evidence was sufficient.",
        ),
    ]

    result = await format_node(state)

    assert result["job_result"].status == "completed"


@pytest.mark.asyncio
async def test_format_consolidates_evidence_aliases_and_filters_complementary_issue(base_state):
    state = base_state.copy()
    requirement = _req(1, ["FR"])
    requirement.evidence = []
    state["classified_requirements"] = [requirement]
    state["user_stories"] = [_story("s1", 1, ["FR"])]
    state["quality_issues"] = [
        QualityIssue(item_id=1, item_type="requirement", severity="medium", rule_violated="evidence_semantic_mismatch", details="Candidate did not match."),
        QualityIssue(item_id=1, item_type="requirement", severity="high", rule_violated="missing_verified_evidence", details="No candidate was verified."),
        QualityIssue(item_id=1, item_type="requirement", severity="high", rule_violated="missing_evidence", details="Evidence is missing."),
        QualityIssue(item_id=1, item_type="requirement", severity="medium", rule_violated="semantic_conflict_complementary", details="Related requirement."),
    ]

    result = await format_node(state)
    job = result["job_result"]

    assert len(job.quality_issues) == 1
    assert job.quality_issues[0].rule_violated == "missing_verified_evidence"
    assert job.requirements[0].quality.score == 0.85
    assert job.user_stories[0].quality.score == 0.85
