"""Phase 7 — derived quality scoring."""

from __future__ import annotations

from app.schemas.items import (
    AcceptanceCriterion,
    ClassifiedRequirement,
    EvidenceSpan,
    UserStory,
)
from app.services.quality_scoring import compute_quality_scores


def _req(rid, *, evidence=True, quote_support=None, labels=None):
    return ClassifiedRequirement(
        id=rid, text=f"req {rid}", candidate_labels=labels or ["FR"], labels=labels or ["FR"],
        confidence=0.9, classification_confidence=0.9,
        evidence=[EvidenceSpan(chunk_id="c", quote="q")] if evidence else [],
        quote_support_score=quote_support,
    )


def _ac(text, i=1):
    return AcceptanceCriterion(id=f"ac{i}", text=text, criterion_type="Given-When-Then")


def _story(sid, *, acs=2, generic=False, source_ids=(1,), title="Story"):
    if generic:
        criteria = [_ac("works as expected", i) for i in range(acs)]
    else:
        criteria = [_ac(f"Given state {i}, when action {i}, then result {i} occurs clearly.", i) for i in range(acs)]
    return UserStory(
        id=sid, title=title, description="As a user, I want X, so that Y.",
        acceptance_criteria=criteria, source_requirement_ids=list(source_ids),
        labels=["FR"], evidence_reference=[],
    )


def test_clean_run_scores_high():
    reqs = [_req(1, evidence=True, quote_support=1.0)]
    stories = [_story("US1")]
    s = compute_quality_scores(reqs, stories, [])
    assert s.traceability_coverage == 1.0
    assert s.groundedness_score == 1.0
    assert s.story_completeness == 1.0
    assert s.acceptance_criteria_quality == 1.0
    assert s.duplicate_risk == 0.0
    assert s.overall_score == 1.0


def test_missing_evidence_lowers_groundedness():
    reqs = [_req(1, evidence=True, quote_support=1.0), _req(2, evidence=False)]
    s = compute_quality_scores(reqs, [], [])
    assert s.groundedness_score == 0.5
    assert s.overall_score < 1.0


def test_quote_support_score_drives_groundedness():
    reqs = [_req(1, quote_support=0.0), _req(2, quote_support=1.0)]
    s = compute_quality_scores(reqs, [], [])
    assert s.groundedness_score == 0.5


def test_missing_source_ids_lowers_traceability():
    stories = [_story("US1", source_ids=(1,)), _story("US2", source_ids=())]
    s = compute_quality_scores([_req(1)], stories, [])
    assert s.traceability_coverage == 0.5


def test_generic_criteria_lower_ac_quality():
    stories = [_story("US1", generic=True)]
    s = compute_quality_scores([_req(1)], stories, [])
    assert s.acceptance_criteria_quality == 0.0


def test_insufficient_criteria_lower_completeness():
    stories = [_story("US1", acs=1)]
    s = compute_quality_scores([_req(1)], stories, [])
    assert s.story_completeness == 0.0


def test_duplicate_stories_raise_duplicate_risk():
    a = _story("US1", title="Same")
    b = _story("US2", title="Same")
    s = compute_quality_scores([_req(1)], [a, b], [])
    assert s.duplicate_risk == 0.5


def test_high_severity_issue_counted():
    from app.schemas.items import QualityIssue
    issues = [QualityIssue(item_id=1, item_type="requirement", severity="high", rule_violated="x", details="d")]
    s = compute_quality_scores([_req(1)], [], issues)
    assert s.high_severity_issue_count == 1
