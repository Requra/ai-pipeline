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
    access_control_entails,
    best_evidence_clause,
    clause_coverage,
    complete_requirement_from_evidence,
    has_polarity_conflict,
    infer_requirement_category,
    infer_requirement_priority,
    missing_required_numeric_claims,
    normalize_requirement_labels,
    normalize_story_points,
    numeric_upper_bound_entails,
    split_requirement_clauses,
    unsupported_fact_terms,
    unsupported_review_terms,
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


def test_three_state_polarity():
    from app.services.semantic_quality import evaluate_polarity
    sources = ["The system shall send notifications upon failure."]
    # Entailed
    assert evaluate_polarity("The system sends notifications.", sources) == "ENTAILED"
    # Contradicted
    assert evaluate_polarity("The system shall not send notifications.", sources) == "CONTRADICTED"
    # Not Covered (Omission/Unrelated)
    assert evaluate_polarity("The user can invite collaborators.", sources) == "NOT_COVERED"


def test_polarity_omission_is_not_contradiction():
    from app.services.semantic_quality import evaluate_polarity

    sources = [
        "The report shall summarize response compliance without exposing customer credentials."
    ]

    assert (
        evaluate_polarity("The report is grouped by support team.", sources)
        == "NOT_COVERED"
    )
    assert (
        evaluate_polarity("The report exposes customer credentials.", sources)
        == "CONTRADICTED"
    )


def test_exact_negative_source_sentence_is_entailed_before_clause_comparison():
    from app.services.semantic_quality import evaluate_polarity

    requirement = (
        "The reporting view shall summarize response-time compliance by team, "
        "priority, and calendar month without exposing customer credentials."
    )
    evidence = (
        "The reporting view shall summarize response-time compliance by team,\n"
        "priority, and calendar month without exposing customer credentials."
    )

    assert evaluate_polarity(requirement, [evidence]) == "ENTAILED"


def test_composite_evidence_uses_shortest_exact_supporting_sentence():
    requirement = (
        "Analysts shall attach sanitized diagnostic files to a case; attachments "
        "shall be virus-scanned before they become available to other users."
    )
    source = (
        "The reporting view shall summarize compliance. "
        "Analysts shall attach sanitized diagnostic files to a case; attachments "
        "shall be virus-scanned before they become available to other users. "
        "Weekend travel notes discuss museums, trains, and cafes."
    )

    score, quote = best_evidence_clause(requirement, source)

    assert score == 1.0
    assert quote.startswith("Analysts shall attach")
    assert "virus-scanned" in quote
    assert "reporting view" not in quote.lower()
    assert "travel notes" not in quote.lower()
    assert len(quote) < 250


@pytest.mark.asyncio
async def test_grounding_accepts_exact_requirement_with_without_clause():
    requirement = (
        "The reporting view shall summarize response-time compliance by team, "
        "priority, and calendar month without exposing customer credentials."
    )
    source = requirement + " Review note: validates long-document chunking."
    req = _classified(
        requirement,
        [EvidenceSpan(chunk_id="reporting", quote=source, document_id="ops")],
    )

    result = await evidence_grounding_node({
        "classified_requirements": [req],
        "chunks": [_chunk("reporting", source, "ops")],
        "source_documents": [{"source_id": "ops", "language": "en"}],
        "quality_issues": [],
    })

    evidence = result["classified_requirements"][0].evidence
    assert len(evidence) == 1
    assert evidence[0].quote == requirement
    assert not any(
        issue.rule_violated in {
            "evidence_semantic_mismatch",
            "missing_verified_evidence",
        }
        for issue in result["quality_issues"]
    )


def test_behavior_synonyms_do_not_create_false_unsupported_facts():
    sources = [
        "The notification service shall alert account owners when an export is downloaded.",
        "The system shall record immutable audit events.",
    ]

    assert not unsupported_fact_terms(
        "As an owner, I want to be notified when an export is downloaded.",
        sources,
    )
    assert not unsupported_fact_terms(
        "As an operator, I want to maintain an immutable audit log.",
        sources,
    )


def test_clause_coverage_with_contradiction():
    from app.services.semantic_quality import clause_coverage
    class Req:
        def __init__(self, text):
            self.text = text
            
    req = Req("The system shall send notifications and record audit logs.")
    # Both clauses covered
    assert clause_coverage([req], ["send notifications", "record audit logs"]) == 1.0
    # One clause covered, one contradicted (so not covered)
    assert clause_coverage([req], ["send notifications", "do not record audit logs"]) == 0.5
    # One clause covered, one omitted (NOT_COVERED)
    assert clause_coverage([req], ["send notifications"]) == 0.5


def test_quality_scoring_deduplication_and_exclusions():
    from app.schemas.items import QualityIssue, ClassifiedRequirement, UserStory, AcceptanceCriterion
    
    req = ClassifiedRequirement(
        id=1, text="The system shall do X.", candidate_labels=["FR"], labels=["FR"],
        confidence=0.9, classification_confidence=0.9,
        evidence=[EvidenceSpan(chunk_id="c", quote="q")],
        quote_support_score=0.9,
    )
    story = UserStory(
        id="US-1", title="Test story", description="As a user, I want X, so that Y.",
        source_requirement_ids=[1], labels=["FR"],
        acceptance_criteria=[
            AcceptanceCriterion(id="AC-1", text="Given X, when Y, then Z.", criterion_type="Given-When-Then"),
            AcceptanceCriterion(id="AC-2", text="Given A, when B, then C.", criterion_type="Given-When-Then")
        ]
    )
    
    # Evidence issues (represented by groundedness_score < 1.0) and COMPLEMENTARY conflicts
    issues = [
        QualityIssue(item_id=1, item_type="requirement", severity="high", rule_violated="missing_verified_evidence", details=""),
        QualityIssue(item_id=1, item_type="requirement", severity="high", rule_violated="missing_evidence", details=""),
        # Complementary conflict (informational)
        QualityIssue(item_id=1, item_type="requirement", severity="high", rule_violated="semantic_conflict_complementary", details=""),
        # Diagnostic issue
        QualityIssue(item_id=1, item_type="requirement", severity="medium", rule_violated="priority_not_source_supported", details=""),
    ]
    
    scores = compute_quality_scores([req], [story], issues)
    # The score should NOT be capped at 0.59 or penalized by the above issues
    # because they are either represented, diagnostic, or complementary!
    assert scores.overall_score > 0.79


@pytest.mark.asyncio
async def test_evidence_grounding_decision_flow_accept():
    from app.nodes.evidence_grounding import evidence_grounding_node
    req = _classified(
        "The system shall display reports.",
        [EvidenceSpan(chunk_id="c1", quote="The system shall display reports.", document_id="doc1")]
    )
    chunks = [_chunk("c1", "The system shall display reports.", "doc1")]
    state = {
        "classified_requirements": [req],
        "chunks": chunks,
        "source_documents": [{"source_id": "doc1", "language": "en"}],
        "language": "en"
    }
    result = await evidence_grounding_node(state)
    assert len(result["classified_requirements"][0].evidence) == 1
    assert result["classified_requirements"][0].needs_review is False


@pytest.mark.asyncio
async def test_evidence_grounding_decision_flow_reject_mismatch():
    from app.nodes.evidence_grounding import evidence_grounding_node
    req = _classified(
        "The system shall not display reports.",
        [EvidenceSpan(chunk_id="c1", quote="The system shall display reports.", document_id="doc1")]
    )
    chunks = [_chunk("c1", "The system shall display reports.", "doc1")]
    state = {
        "classified_requirements": [req],
        "chunks": chunks,
        "source_documents": [{"source_id": "doc1", "language": "en"}],
        "language": "en"
    }
    result = await evidence_grounding_node(state)
    assert len(result["classified_requirements"][0].evidence) == 0
    assert result["classified_requirements"][0].needs_review is True
    assert any(iss.rule_violated == "evidence_semantic_mismatch" for iss in result["quality_issues"])


@pytest.mark.asyncio
async def test_evidence_grounding_decision_flow_partial_support():
    from app.nodes.evidence_grounding import evidence_grounding_node
    req = _classified(
        "The system displays case files.",
        [EvidenceSpan(chunk_id="c1", quote="The system displays.", document_id="doc1")]
    )
    chunks = [_chunk("c1", "The system displays.", "doc1")]
    state = {
        "classified_requirements": [req],
        "chunks": chunks,
        "source_documents": [{"source_id": "doc1", "language": "en"}],
        "language": "en"
    }
    result = await evidence_grounding_node(state)
    assert len(result["classified_requirements"][0].evidence) == 0
    assert result["classified_requirements"][0].needs_review is True
    assert any(
        issue.rule_violated == "evidence_semantic_mismatch"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_evidence_grounding_decision_flow_different_languages():
    from app.nodes.evidence_grounding import evidence_grounding_node
    req = _classified(
        "The system shall translate Arabic.",
        [EvidenceSpan(chunk_id="c1", quote="النظام يجب أن يترجم العربية.", document_id="doc1")]
    )
    chunks = [_chunk("c1", "النظام يجب أن يترجم العربية.", "doc1")]
    state = {
        "classified_requirements": [req],
        "chunks": chunks,
        "source_documents": [{"source_id": "doc1", "language": "ar"}],
        "language": "en"
    }
    result = await evidence_grounding_node(state)
    assert len(result["classified_requirements"][0].evidence) == 0
    assert result["classified_requirements"][0].needs_review is True


@pytest.mark.asyncio
async def test_low_confidence_audio_evidence_is_retained_for_review():
    from app.nodes.evidence_grounding import evidence_grounding_node

    req = _classified(
        "The system shall display reports.",
        [
            EvidenceSpan(
                chunk_id="audio-1",
                quote="The system shall display reports.",
                timestamp="12.4",
            )
        ],
    )
    chunk = _chunk("audio-1", "The system shall display reports.", None)
    chunk.start_time_sec = 12.4
    chunk.end_time_sec = 15.0
    chunk.speaker = "2"
    chunk.language = "en"
    chunk.asr_confidence = 0.42

    result = await evidence_grounding_node(
        {
            "classified_requirements": [req],
            "chunks": [chunk],
            "source_documents": [],
            "quality_issues": [],
        }
    )

    grounded = result["classified_requirements"][0]
    assert len(grounded.evidence) == 1
    assert grounded.needs_review is True
    assert any(
        issue.rule_violated == "evidence_low_transcription_confidence"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_low_confidence_audio_does_not_bypass_semantic_rejection():
    from app.nodes.evidence_grounding import evidence_grounding_node

    req = _classified(
        "The system shall display reports.",
        [
            EvidenceSpan(
                chunk_id="audio-unrelated",
                quote="The cafeteria closes after lunch.",
            )
        ],
    )
    chunk = _chunk(
        "audio-unrelated",
        "The cafeteria closes after lunch.",
        None,
    )
    chunk.language = "en"
    chunk.asr_confidence = 0.30

    result = await evidence_grounding_node(
        {
            "classified_requirements": [req],
            "chunks": [chunk],
            "source_documents": [],
            "quality_issues": [],
        }
    )

    assert result["classified_requirements"][0].evidence == []


@pytest.mark.asyncio
async def test_grounding_uses_best_supporting_clause_from_declared_chunk():
    source = (
        "The queue displays pending support cases. "
        "Each status transition must preserve the prior value, the acting user, "
        "the timestamp, and a human-readable rationale in the case history. "
        "Weekend travel notes are out of scope."
    )
    requirement = (
        "Each status transition shall retain the prior value, acting user, "
        "timestamp, and human-readable rationale in case history."
    )
    req = _classified(
        requirement,
        [EvidenceSpan(chunk_id="case-page", quote=source, document_id="ops-doc")],
    )

    result = await evidence_grounding_node({
        "classified_requirements": [req],
        "chunks": [_chunk("case-page", source, "ops-doc")],
        "source_documents": [{"source_id": "ops-doc", "language": "en"}],
        "quality_issues": [],
    })

    evidence = result["classified_requirements"][0].evidence
    assert len(evidence) == 1
    assert evidence[0].support_score >= 0.60
    assert evidence[0].quote.startswith("Each status transition")
    assert "Weekend travel" not in evidence[0].quote


def test_exclusive_permission_entails_denial_for_other_roles():
    sources = [
        "The application shall allow only administrators to retrieve a retained report."
    ]
    criterion = (
        "Given a retained report, when a non-administrator retrieves it, "
        "then access is denied."
    )

    assert access_control_entails(criterion, sources)
    assert "deny" not in unsupported_fact_terms(criterion, sources)


def test_gwt_context_is_used_for_acceptance_criteria_coverage():
    req = _classified(
        "The audit search screen shall allow filtering by actor, action, "
        "target project, and a caller-selected date range."
    )
    criteria = [
        (
            "Given the audit search screen, when a user filters by actor, action, "
            "target project, and a selected date range, then matching audit events "
            "are displayed."
        )
    ]

    assert clause_coverage([req], criteria) == 1.0


@pytest.mark.asyncio
async def test_valid_paraphrased_story_is_not_reported_as_wrong_mapping(base_state):
    source = (
        "The workspace shall require multi-factor authentication for administrators "
        "before they can change organization settings or billing contacts."
    )
    requirement = _classified(
        source,
        [EvidenceSpan(chunk_id="mfa", quote=source, support_score=1.0)],
    )
    story = _story(
        "As an administrator, I want to secure settings and billing changes via MFA, "
        "so that unauthorized changes are prevented.",
        [
            "Given an administrator, when organization settings are changed, then multi-factor authentication is required.",
            "Given an administrator, when billing contacts are changed, then MFA is required.",
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

    assert not any(
        issue.rule_violated == "incorrect_story_requirement_mapping"
        for issue in result["quality_issues"]
    )


@pytest.mark.asyncio
async def test_exclusive_access_negative_ac_is_supported_and_covered(base_state):
    source = (
        "The application shall allow only administrators to retrieve a retained report."
    )
    requirement = _classified(
        source,
        [EvidenceSpan(chunk_id="access", quote=source, support_score=1.0)],
    )
    story = _story(
        "As an administrator, I want to retrieve retained reports, so that access "
        "is limited to administrators.",
        [
            "Given a retained report, when an administrator retrieves it, then retrieval succeeds.",
            "Given a retained report, when a non-administrator retrieves it, then access is denied.",
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
    story_rules = {
        issue.rule_violated
        for issue in result["quality_issues"]
        if issue.item_type == "story"
    }

    assert "acceptance_criterion_unsupported_fact" not in story_rules
    assert "acceptance_criteria_missing_source_clause" not in story_rules


@pytest.mark.asyncio
async def test_low_alignment_alone_creates_review_not_high_mapping_issue(base_state):
    source = "Operators shall reconcile submitted settlement records."
    requirement = _classified(
        source,
        [EvidenceSpan(chunk_id="settlement", quote=source, support_score=1.0)],
    )
    story = _story(
        "As a specialist, I want a workspace overview, so that daily work is visible.",
        [
            "Given the workspace, when it opens, then current information is visible.",
            "Given daily work, when it changes, then the overview remains available.",
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
    mapping = [
        issue
        for issue in result["quality_issues"]
        if issue.rule_violated == "incorrect_story_requirement_mapping"
    ]

    assert len(mapping) == 1
    assert mapping[0].severity == "medium"


def test_decimal_and_protocol_values_do_not_split_evidence_clauses():
    source = (
        "The dashboard shall load in less than 2.0 seconds under 500 sessions. "
        "All traffic shall use TLS 1.3 protocol. "
        "Availability shall be at least 99.9% monthly."
    )
    requirements = [
        "The dashboard shall load in less than 2.0 seconds under 500 sessions.",
        "All traffic shall use TLS 1.3 protocol.",
        "Availability shall be at least 99.9% monthly.",
    ]

    for requirement in requirements:
        score, quote = best_evidence_clause(requirement, source)
        assert score >= 0.90
        assert requirement.rstrip(".") in quote


def test_category_inference_distinguishes_performance_reporting_and_security():
    assert infer_requirement_category(
        "The dashboard shall load and become interactive within 2 seconds under concurrent load.",
        ["NFR"],
    ) == "Performance & Reliability"
    assert infer_requirement_category(
        "The analytics dashboard shall export a monthly CSV report.",
        ["FR"],
    ) == "Reporting & Export"
    assert infer_requirement_category(
        "All browser traffic shall use TLS 1.3.",
        ["NFR"],
    ) == "Security & Access Control"


def test_label_normalization_removes_spurious_nfr_from_concrete_capability():
    assert normalize_requirement_labels(
        "The system shall generate a unique QR code for each asset for fast scanning.",
        ["NFR", "FR"],
    ) == ["FR"]
    assert normalize_requirement_labels(
        "The dashboard shall load within 2.0 seconds.",
        ["FR", "NFR"],
    ) == ["FR", "NFR"]


def test_evidence_with_additional_numeric_constraint_is_support_not_contradiction():
    requirement = (
        "The dashboard shall load in less than 2.0 seconds under normal concurrent load."
    )
    evidence = (
        "The dashboard must load in less than 2.0 seconds under normal concurrent "
        "load (up to 500 active sessions)."
    )

    score, clause = best_evidence_clause(requirement, evidence)

    assert score >= 0.60
    assert "500 active sessions" in clause
    assert complete_requirement_from_evidence(requirement, clause) == evidence


def test_soft_delete_entails_record_retention_and_normalizes_delete_forms():
    source = (
        "Asset records cannot be permanently deleted; they must be soft-deleted "
        "and marked as Retired for audit compliance."
    )

    assert not unsupported_fact_terms(
        "The system shall soft-delete records instead of permanently deleting them.",
        [source],
    )
    assert not unsupported_fact_terms(
        "Asset history is preserved and retained.",
        [source],
    )


def test_label_normalization_removes_unsupported_business_rule_label():
    assert normalize_requirement_labels(
        "The system shall register a hardware asset.",
        ["FR", "BR"],
    ) == ["FR"]
    assert normalize_requirement_labels(
        "Requests exceeding $1,000 require manager approval.",
        ["FR", "BR"],
    ) == ["FR", "BR"]


def test_observable_actions_absent_from_source_are_rejected():
    assert unsupported_fact_terms(
        "The new asset is included in the asset list.",
        ["The administrator shall register a hardware asset."],
    ) == {"include"}
    assert unsupported_fact_terms(
        "The checkout request status is updated accordingly.",
        ["A user may request an asset checkout."],
    ) == {"update"}


def test_passive_notifications_and_recording_are_reviewed_as_new_behavior():
    source = "A user may request an asset checkout."
    assert unsupported_review_terms(
        "The checkout request is recorded in the system.",
        [source],
    ) == {"record"}
    assert unsupported_review_terms(
        "The user is informed of the checkout limit.",
        [source],
    ) == {"notify"}


@pytest.mark.parametrize(
    ("candidate", "source", "expected"),
    [
        (
            "Then the uploaded asset appears in the asset list.",
            "Users shall upload an asset.",
            "display",
        ),
        (
            "Then the request is listed with status and details.",
            "Users shall submit a request.",
            "display",
        ),
        (
            "Then appropriate access is granted based on the LDAP profile.",
            "The application shall authenticate users through LDAP.",
            "authorize",
        ),
    ],
)
def test_unsupported_outcomes_are_rejected_generically(candidate, source, expected):
    assert expected in unsupported_fact_terms(candidate, [source])


def test_negative_source_constraint_is_restored_from_verified_evidence():
    requirement = "Records shall be soft-deleted and marked as Retired."
    evidence = (
        "Records cannot be permanently deleted; they must be soft-deleted "
        "and marked as Retired."
    )
    assert complete_requirement_from_evidence(requirement, evidence) == evidence


def test_conjunct_notification_and_accessibility_are_unsupported_outcomes():
    limit_source = "Users shall be allowed to check out up to 3 assets simultaneously."
    assert unsupported_review_terms(
        "Then the system prevents checkout and informs the user of the limit.",
        [limit_source],
    ) == {"notify"}
    assert has_polarity_conflict(
        "Users can obtain assets without restrictions.",
        [limit_source],
    )
    assert "access" in unsupported_fact_terms(
        "Then the retired record is accessible during an audit.",
        ["Records shall be soft-deleted and marked as Retired for audit compliance."],
    )


def test_reordered_positive_and_negative_clauses_are_not_a_contradiction():
    source = (
        "Asset database records cannot be permanently deleted; they must be "
        "soft-deleted and marked as Retired for audit compliance."
    )
    requirement = (
        "Asset database records shall be soft-deleted and marked as Retired for "
        "audit compliance, and not permanently deleted."
    )
    assert not has_polarity_conflict(requirement, [source])


def test_numeric_upper_bound_entails_rejection_above_the_limit():
    source = "Users shall be allowed to check out up to 3 assets simultaneously."
    boundary = (
        "Given a user already has 3 assets, when the user attempts to check out "
        "another asset, then the operation does not proceed because the maximum "
        "of 3 has been reached."
    )
    assert numeric_upper_bound_entails(boundary, [source])
    assert not unsupported_fact_terms(boundary, [source])
    assert not has_polarity_conflict(boundary, [source])
    assert numeric_upper_bound_entails(
        "Given 3 checked-out assets, when another is requested, then the system prevents the additional checkout.",
        [source],
    )


def test_workload_envelope_does_not_entail_rejection_above_the_test_load():
    source = (
        "The dashboard shall load in less than 2 seconds under normal concurrent "
        "load of up to 500 active sessions."
    )
    candidate = (
        "Given 500 active sessions, when another session begins, then the system "
        "prevents the additional session."
    )

    assert not numeric_upper_bound_entails(candidate, [source])


def test_clause_coverage_requires_all_source_numeric_values():
    requirement = ClassifiedRequirement(
        id=1,
        text=(
            "The dashboard shall load in less than 2.0 seconds under normal "
            "concurrent load of up to 500 active sessions."
        ),
        candidate_labels=["NFR"], labels=["NFR"], confidence=0.9,
    )
    vague = (
        "Given normal load, when users open the dashboard, then it loads within "
        "the specified time."
    )
    measurable = (
        "Given up to 500 active sessions, when users open the dashboard, then it "
        "loads in less than 2 seconds."
    )
    assert missing_required_numeric_claims(requirement.text, vague) == {"2", "500"}
    assert clause_coverage([requirement], [vague]) == 0.0
    assert not missing_required_numeric_claims(requirement.text, measurable)
    assert clause_coverage([requirement], [measurable]) == 1.0
