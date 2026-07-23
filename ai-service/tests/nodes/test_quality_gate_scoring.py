"""Phase 7 — quality gate scoring + new meaningful issues."""

from __future__ import annotations

import pytest

from app.nodes.quality_gate import quality_gate_node
from app.schemas.items import (
    AcceptanceCriterion,
    ClassifiedRequirement,
    EvidenceSpan,
    UserStory,
)


def _req(rid, *, conf=0.9, evidence=True, labels=None):
    return ClassifiedRequirement(
        id=rid, text=f"Requirement {rid}", candidate_labels=labels or ["FR"], labels=labels or ["FR"],
        confidence=conf, classification_confidence=conf,
        evidence=[EvidenceSpan(chunk_id="c", quote="q")] if evidence else [],
    )


def _ac(text, sid, i):
    return AcceptanceCriterion(id=f"{sid}_ac_{i}", text=text, criterion_type="Given-When-Then")


def _story(sid, *, generic=False, source_ids=(1,), title="Story"):
    crit = ([_ac("works as expected", sid, 1), _ac("as specified", sid, 2)] if generic
            else [_ac("Given a user, when they act, then a clear result occurs.", sid, 1),
                  _ac("Given invalid input, when they act, then an error is shown.", sid, 2)])
    return UserStory(
        id=sid, title=title, description="As a user, I want X, so that Y.",
        acceptance_criteria=crit, source_requirement_ids=list(source_ids),
        labels=["FR"], evidence_reference=[EvidenceSpan(chunk_id="c", quote="q")],
    )


def _state(base_state, reqs, stories):
    s = base_state.copy()
    s["classified_requirements"] = reqs
    s["user_stories"] = stories
    s["requirement_coverages"] = []
    s["quality_issues"] = []
    return s


@pytest.mark.asyncio
async def test_quality_gate_emits_quality_report(base_state):
    out = await quality_gate_node(_state(base_state, [_req(1)], [_story("US1")]))
    report = out["quality_report"]
    assert report is not None
    assert 0.0 <= report["overall_score"] <= 1.0
    assert report["requirement_count"] == 1
    assert report["story_count"] == 1


@pytest.mark.asyncio
async def test_generic_acceptance_criteria_flagged(base_state):
    out = await quality_gate_node(_state(base_state, [_req(1)], [_story("US1", generic=True)]))
    assert any(q.rule_violated == "generic_acceptance_criteria" for q in out["quality_issues"])
    assert out["quality_report"]["acceptance_criteria_quality"] == 0.0


@pytest.mark.asyncio
async def test_low_confidence_classification_flagged(base_state):
    out = await quality_gate_node(_state(base_state, [_req(1, conf=0.2)], [_story("US1")]))
    assert any(q.rule_violated == "low_confidence_classification" for q in out["quality_issues"])


@pytest.mark.asyncio
async def test_duplicate_story_flagged(base_state):
    dup_a = _story("US1", title="Same")
    dup_b = _story("US2", title="Same")
    out = await quality_gate_node(_state(base_state, [_req(1)], [dup_a, dup_b]))
    assert any(q.rule_violated == "duplicate_story" for q in out["quality_issues"])
    assert out["quality_report"]["duplicate_risk"] == 0.5


@pytest.mark.asyncio
async def test_missing_evidence_lowers_groundedness_in_report(base_state):
    out = await quality_gate_node(_state(base_state, [_req(1, evidence=False)], []))
    # Missing evidence also yields a high-severity issue -> needs_review.
    assert out["status"] == "needs_review"
    assert out["quality_report"]["groundedness_score"] == 0.0


@pytest.mark.asyncio
async def test_issues_are_not_duplicated(base_state):
    # Same requirement that triggers multiple checks should not duplicate issues.
    out = await quality_gate_node(_state(base_state, [_req(1, conf=0.2, evidence=False)], []))
    issues = out["quality_issues"]
    keys = {(q.item_id, q.item_type, q.rule_violated, q.details) for q in issues}
    assert len(keys) == len(issues)


@pytest.mark.asyncio
async def test_story_quality_issues_use_stable_story_indexes(base_state):
    first = _story("job_story_a", title="")
    second = _story("job_story_b", title="")

    out = await quality_gate_node(
        _state(base_state, [_req(1)], [first, second])
    )

    empty_title_ids = {
        issue.item_id
        for issue in out["quality_issues"]
        if issue.rule_violated == "story_empty_title"
    }
    assert empty_title_ids == {1, 2}


@pytest.mark.asyncio
async def test_notification_synonym_does_not_create_high_story_fact(base_state):
    requirement = ClassifiedRequirement(
        id=1,
        text="The service shall alert account owners when an export is downloaded.",
        candidate_labels=["FR"],
        labels=["FR"],
        confidence=0.9,
        classification_confidence=0.9,
        evidence=[
            EvidenceSpan(
                chunk_id="c",
                quote="The service shall alert account owners when an export is downloaded.",
            )
        ],
    )
    story = UserStory(
        id="job_story_1",
        title="Notify owners about exports",
        description=(
            "As an account owner, I want to be notified when an export is "
            "downloaded, so that I can monitor access."
        ),
        acceptance_criteria=[
            _ac(
                "Given an export, when it is downloaded, then the account owner is notified.",
                "job_story_1",
                1,
            ),
            _ac(
                "Given an account owner, when an export download occurs, then an alert is sent.",
                "job_story_1",
                2,
            ),
        ],
        source_requirement_ids=[1],
        labels=["FR"],
        evidence_reference=requirement.evidence,
        story_points=3,
    )

    out = await quality_gate_node(_state(base_state, [requirement], [story]))

    assert not any(
        issue.rule_violated == "story_unsupported_fact"
        for issue in out["quality_issues"]
    )
