"""Phase 6 — story validator unit tests."""

from __future__ import annotations

from app.schemas.items import AcceptanceCriterion, EvidenceSpan, ExtractedRequirement, UserStory
from app.validators.story_validator import (
    find_duplicate_story_ids,
    is_generic_ac,
    validate_story,
)


def _ac(text, i=1):
    return AcceptanceCriterion(id=f"s_ac_{i}", text=text, criterion_type="Given-When-Then")


def _story(**over):
    base = dict(
        id="US1",
        title="Some story",
        description="As a user, I want to do X, so that Y.",
        acceptance_criteria=[_ac("Given A, when B, then C happens clearly.", 1),
                             _ac("Given D, when E, then F is rejected with an error.", 2)],
        source_requirement_ids=[1],
        labels=["FR"],
        evidence_reference=[EvidenceSpan(chunk_id="c1", quote="q")],
    )
    base.update(over)
    return UserStory(**base)


def test_is_generic_ac():
    assert is_generic_ac("Requirement is implemented as specified")
    assert is_generic_ac("works as expected")
    assert is_generic_ac("")
    assert is_generic_ac("ok")  # too short
    assert not is_generic_ac("Given a valid order, when submitted, then it is accepted.")


def test_validate_clean_story_has_no_issues():
    req = ExtractedRequirement(id=1, text="t", candidate_labels=["FR"], confidence=0.9,
                               evidence=[EvidenceSpan(chunk_id="c1", quote="q")])
    assert validate_story(_story(), {1: req}) == []


def test_validate_flags_insufficient_acceptance_criteria():
    issues = validate_story(_story(acceptance_criteria=[_ac("Given A, when B, then C is done.")]), {})
    assert "insufficient_acceptance_criteria" in issues


def test_validate_flags_all_generic_acceptance_criteria():
    s = _story(acceptance_criteria=[_ac("Requirement is implemented as specified", 1),
                                    _ac("works as expected", 2)])
    issues = validate_story(s, {})
    assert "all_generic_acceptance_criteria" in issues


def test_validate_flags_missing_source_ids():
    assert "missing_source_requirement_ids" in validate_story(_story(source_requirement_ids=[]), {})


def test_validate_flags_missing_evidence_reference_when_source_has_evidence():
    req = ExtractedRequirement(id=1, text="t", candidate_labels=["FR"], confidence=0.9,
                               evidence=[EvidenceSpan(chunk_id="c1", quote="q")])
    s = _story(evidence_reference=[])
    assert "missing_evidence_reference" in validate_story(s, {1: req})


def test_find_duplicate_story_ids():
    a = _story(id="US1", title="Same", description="As a user, I want X, so that Y.")
    b = _story(id="US2", title="Same", description="As a user, I want X, so that Y.")
    c = _story(id="US3", title="Different", description="As a user, I want Z, so that W.")
    dupes = find_duplicate_story_ids([a, b, c])
    assert dupes == ["US2"]
