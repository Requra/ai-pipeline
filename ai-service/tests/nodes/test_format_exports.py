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


def _req(rid, labels, conf=0.9):
    return ClassifiedRequirement(
        id=rid, text=f"Requirement {rid}", actor="user", goal="do thing",
        candidate_labels=labels, labels=labels, confidence=conf, classification_confidence=conf,
        evidence=[EvidenceSpan(chunk_id=f"c{rid}", quote=f"quote for {rid}")],
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
    assert row["confidence"] == 0.77
    assert "Given A" in row["acceptance_criteria"]
    assert "quote for 1" in row["source_quotes"]
    assert row["type"] == "Functional"
    # Jira rows carry acceptance criteria + traceability.
    jira = jr.exports.jira.rows[0]
    assert len(jira["acceptance_criteria"]) == 2
    assert "quote for 1" in jira["source_quotes"]


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
