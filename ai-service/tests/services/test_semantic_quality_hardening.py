"""Regression tests for source-aware evidence and honest quality scores."""

from __future__ import annotations

import pytest

from app.nodes.evidence_grounding import evidence_grounding_node
from app.nodes.quality_gate import quality_gate_node
from app.nodes.retrieve_evidence import retrieve_evidence_node
from app.rag.source_index import build_source_index, clear_source_index
from app.schemas.items import (
    AcceptanceCriterion,
    ClassifiedRequirement,
    EvidenceSpan,
    ExtractedRequirement,
    QualityIssue,
    SourceChunk,
    UserStory,
)
from app.services.quality_scoring import compute_quality_scores
from app.services.semantic_quality import (
    infer_requirement_category,
    infer_requirement_priority,
    normalize_story_points,
    split_requirement_clauses,
    unsupported_fact_terms,
)


def _chunk(chunk_id: str, text: str, document_id: str = "doc-1") -> SourceChunk:
    return SourceChunk(
        chunk_id=chunk_id,
        text=text,
        start_char=0,
        end_char=len(text),
        document_id=document_id,
    )


def _classified(text: str, evidence=None) -> ClassifiedRequirement:
    return ClassifiedRequirement(
        id=1,
        text=text,
        candidate_labels=["FR"],
        labels=["FR"],
        confidence=0.9,
        classification_confidence=0.9,
        evidence=evidence or [],
    )


def _story(description: str, criteria: list[str]) -> UserStory:
    return UserStory(
        id="US-1",
        title="Test story",
        description=description,
        source_requirement_ids=[1],
        labels=["FR"],
        acceptance_criteria=[
            AcceptanceCriterion(id=f"AC-{i}", text=text, criterion_type="Given-When-Then")
            for i, text in enumerate(criteria, start=1)
        ],
    )


@pytest.mark.asyncio
async def test_retrieval_does_not_append_unrelated_top_hits():
    job_id = "semantic-retrieval"
    chunks = [
        _chunk("c1", "The workspace requires multi-factor authentication for administrators."),
        _chunk("c2", "Garden volunteers discussed compost and tomato seedlings."),
    ]
    build_source_index(job_id, chunks)
    req = ExtractedRequirement(
        id=1,
        text="Attachments shall be virus-scanned before other users can access them.",
        candidate_labels=["FR"],
        confidence=0.9,
        evidence=[],
    )
    result = await retrieve_evidence_node({
        "job_id": job_id,
        "source_index_id": job_id,
        "chunks": chunks,
        "extracted_requirements": [req],
        "warnings": [],
    })
    assert result["extracted_requirements"][0].evidence == []
    assert any(w.code == "NO_RETRIEVED_EVIDENCE" for w in result["warnings"])
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_grounding_rejects_quote_found_only_in_another_chunk():
    quote = "Attachments shall be virus-scanned before access."
    chunks = [_chunk("correct", quote), _chunk("declared", "Unrelated response target text.")]
    req = _classified(
        quote,
        [EvidenceSpan(chunk_id="declared", quote=quote, document_id="doc-1")],
    )
    result = await evidence_grounding_node({
        "job_id": "g1",
        "chunks": chunks,
        "classified_requirements": [req],
        "quality_issues": [],
    })
    assert req.evidence == []
    assert any(issue.rule_violated == "evidence_not_grounded" for issue in result["quality_issues"])


@pytest.mark.asyncio
async def test_grounding_rejects_document_mismatch():
    quote = "The report shall include the source period and timestamp."
    req = _classified(
        quote,
        [EvidenceSpan(chunk_id="c1", quote=quote, document_id="wrong-doc")],
    )
    result = await evidence_grounding_node({
        "job_id": "g2",
        "chunks": [_chunk("c1", quote, document_id="actual-doc")],
        "classified_requirements": [req],
        "quality_issues": [],
    })
    assert req.evidence == []
    assert any(issue.rule_violated == "evidence_document_mismatch" for issue in result["quality_issues"])


def test_evidence_presence_alone_is_not_full_groundedness():
    req = _classified(
        "Attachments shall be virus-scanned before access.",
        [EvidenceSpan(chunk_id="c1", quote="Unrelated MFA text", support_score=0.0)],
    )
    req.quote_support_score = 0.0
    scores = compute_quality_scores([req], [], [])
    assert scores.groundedness_score == 0.0
    assert scores.overall_score < 1.0


@pytest.mark.asyncio
async def test_quality_gate_detects_wrong_story_mapping(base_state):
    requirement = _classified(
        "The application shall retain exported reports for thirty days for administrators.",
        [EvidenceSpan(
            chunk_id="c1",
            quote="The application shall retain exported reports for thirty days for administrators.",
            support_score=1.0,
        )],
    )
    story = _story(
        "As an account owner, I want notifications when administrator roles change, so that I stay informed.",
        [
            "Given a role change, when an administrator is granted, then the account owner is notified.",
            "Given an export download, when it completes, then the account owner is notified.",
        ],
    )
    state = base_state.copy()
    state.update({
        "classified_requirements": [requirement],
        "user_stories": [story],
        "requirement_coverages": [],
        "quality_issues": [],
    })
    result = await quality_gate_node(state)
    assert any(
        issue.rule_violated == "incorrect_story_requirement_mapping"
        for issue in result["quality_issues"]
    )
    assert result["quality_report"]["traceability_coverage"] == 0.0
    assert result["quality_report"]["overall_score"] <= 0.59


@pytest.mark.asyncio
async def test_quality_gate_penalizes_unsupported_numeric_acceptance_fact(base_state):
    source = "Administrators must use multi-factor authentication before changing billing contacts."
    requirement = _classified(
        source,
        [EvidenceSpan(chunk_id="c1", quote=source, support_score=1.0)],
    )
    story = _story(
        "As an administrator, I want multi-factor authentication before changing billing contacts, so that access is secure.",
        [
            "Given an administrator, when MFA succeeds, then billing contacts can be changed within 2 seconds.",
            "Given an administrator without MFA, when a change is attempted, then the change is rejected.",
        ],
    )
    state = base_state.copy()
    state.update({
        "classified_requirements": [requirement],
        "user_stories": [story],
        "requirement_coverages": [],
        "quality_issues": [],
    })
    result = await quality_gate_node(state)
    assert any(
        issue.rule_violated == "acceptance_criterion_unsupported_fact"
        for issue in result["quality_issues"]
    )
    assert result["quality_report"]["acceptance_criteria_quality"] < 1.0


def test_medium_issue_prevents_perfect_overall_score():
    source = "The system shall export reports in PDF format."
    req = _classified(source, [EvidenceSpan(chunk_id="c1", quote=source, support_score=1.0)])
    req.quote_support_score = 1.0
    story = _story(
        "As an administrator, I want to export reports in PDF format, so that I can share them.",
        [
            "Given a report, when PDF export is selected, then a PDF report is generated.",
            "Given an export request, when generation finishes, then the PDF is available.",
        ],
    )
    issue = QualityIssue(
        item_id=1,
        item_type="requirement",
        severity="medium",
        rule_violated="semantic_duplicate",
        details="Duplicate requirement needs review.",
    )
    scores = compute_quality_scores([req], [story], [issue])
    assert scores.overall_score <= 0.79


def test_normative_words_do_not_inflate_backlog_priority():
    assert infer_requirement_priority("The service shall export reports.", "High") == "Medium"
    assert infer_requirement_priority("This is a business-critical requirement.", "Medium") == "Critical"


def test_non_numeric_fact_ledger_rejects_invented_validation_behavior():
    sources = ["The owner shall invite named collaborators to a project."]
    unsupported = unsupported_fact_terms(
        "Given an invalid email, when an invitation is submitted, then an error is displayed.",
        sources,
    )
    assert {"invalid", "error"}.issubset(unsupported)


def test_story_points_are_always_fibonacci():
    assert normalize_story_points(5, ["A simple requirement."]) == 5
    assert normalize_story_points(13, ["The system shall export reports."]) in {1, 2, 3, 5, 8}


def test_requirement_category_is_not_hard_coded_general():
    assert infer_requirement_category("The system shall record immutable audit events.", ["FR"]) == "Audit & Compliance"
    assert infer_requirement_category("Analysts shall update a support case status.", ["FR"]) == "Case Management"


def test_enumerated_source_facts_become_distinct_coverage_clauses():
    clauses = split_requirement_clauses(
        "The system shall record immutable audit events for invitation creation, role changes, sign-in failures, and export requests."
    )
    assert len(clauses) == 4
    assert any("sign-in failures" in clause for clause in clauses)
    assert any("export requests" in clause for clause in clauses)
