"""Phase 6 — story validator unit tests."""

from __future__ import annotations

from app.schemas.items import AcceptanceCriterion, EvidenceSpan, ExtractedRequirement, UserStory
from app.validators.story_validator import (
    find_duplicate_acceptance_criterion_ids,
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
        story_points=3,
        evidence_reference=[EvidenceSpan(chunk_id="c1", quote="q")],
    )
    base.update(over)
    return UserStory(**base)


def test_is_generic_ac():
    assert is_generic_ac("Requirement is implemented as specified")
    assert is_generic_ac("works as expected")
    assert is_generic_ac("")
    assert is_generic_ac("ok")  # too short
    assert is_generic_ac(
        "Given the documented preconditions apply, when the capability is exercised, then the outcome conforms to the requirement."
    )
    assert not is_generic_ac("Given a valid order, when submitted, then it is accepted.")


def test_validate_clean_story_has_no_issues():
    req = ExtractedRequirement(id=1, text="t", candidate_labels=["FR"], confidence=0.9,
                               evidence=[EvidenceSpan(chunk_id="c1", quote="q")])
    assert validate_story(_story(), {1: req}) == []


def test_validate_flags_insufficient_acceptance_criteria():
    issues = validate_story(_story(acceptance_criteria=[]), {})
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


def test_duplicate_acceptance_criteria_detect_semantic_restatement():
    story = _story(acceptance_criteria=[
        _ac(
            "Given a monthly period excluding maintenance, when uptime is measured, then availability is at least 99.9%.",
            1,
        ),
        _ac(
            "Given scheduled maintenance is excluded, when monthly availability is calculated, then uptime meets 99.9%.",
            2,
        ),
    ])

    assert find_duplicate_acceptance_criterion_ids(story) == ["s_ac_2"]
    assert "duplicate_acceptance_criteria" in validate_story(story, {})


def test_distinct_boundary_acceptance_criteria_are_not_duplicates():
    story = _story(acceptance_criteria=[
        _ac(
            "Given three assets are checked out, when a fourth checkout is requested, then the request is denied.",
            1,
        ),
        _ac(
            "Given two assets are checked out, when a third checkout is requested, then the request is allowed.",
            2,
        ),
    ])

    assert find_duplicate_acceptance_criterion_ids(story) == []


def test_distinct_actions_from_one_composite_requirement_are_not_duplicates():
    requirement = ExtractedRequirement(
        id=1,
        text=(
            "The system shall send escalation notifications to the primary on-call "
            "group and retain delivery outcomes for troubleshooting."
        ),
        candidate_labels=["FR"],
        confidence=0.9,
    )
    story = _story(acceptance_criteria=[
        _ac(
            "Given an escalation, when it occurs, then the system sends a notification to the primary on-call group.",
            1,
        ),
        _ac(
            "Given an escalation notification, when delivery completes, then the system retains the delivery outcome for troubleshooting.",
            2,
        ),
    ])

    assert find_duplicate_acceptance_criterion_ids(story, [requirement]) == []


def test_validator_rejects_acceptance_fact_absent_from_verified_evidence():
    requirement = ExtractedRequirement(
        id=1,
        text=(
            "Directory for user authentication. Asset database records cannot be "
            "permanently deleted and must be soft-deleted."
        ),
        candidate_labels=["FR"],
        confidence=0.8,
        evidence=[EvidenceSpan(
            chunk_id="retention",
            quote=(
                "Asset database records cannot be permanently deleted. They must "
                "be soft-deleted."
            ),
        )],
    )
    story = _story(acceptance_criteria=[
        _ac(
            "Given a user signs in, when authentication is requested, then the "
            "system authenticates the user through the directory.",
            1,
        ),
    ])

    issues = validate_story(story, {1: requirement})

    assert "unsupported_acceptance_fact" in issues


def test_validator_keeps_access_denial_entailed_by_exclusive_permission():
    requirement = ExtractedRequirement(
        id=1,
        text="Only administrators may retrieve archived reports.",
        candidate_labels=["FR"],
        confidence=0.9,
        evidence=[EvidenceSpan(
            chunk_id="permissions",
            quote="Only administrators may retrieve archived reports.",
        )],
    )
    story = _story(acceptance_criteria=[
        _ac(
            "Given a non-administrator, when retrieval of an archived report is "
            "requested, then the request is denied.",
            1,
        ),
    ])

    assert "unsupported_acceptance_fact" not in validate_story(story, {1: requirement})
