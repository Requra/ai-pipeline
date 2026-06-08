import pytest
from app.nodes.quality_gate import quality_gate_node
from app.schemas.items import (
    ClassifiedRequirement,
    UserStory,
    AcceptanceCriterion,
    RequirementCoverage,
)


@pytest.mark.asyncio
async def test_quality_gate_flags_story_issues_and_requirement_evidence(base_state):
    state = base_state.copy()

    # Requirement missing evidence
    req = ClassifiedRequirement(
        id=1,
        text="",
        candidate_labels=["FR"],
        confidence=0.5,
        evidence=[],
        labels=["FR"],
        classification_confidence=0.5
    )

    # Story missing acceptance criteria and source ids
    story = UserStory(
        id="s1",
        title="",
        description="No As a pattern",
        acceptance_criteria=[],
        source_requirement_ids=[],
        labels=["FR"],
        evidence_reference=[]
    )

    coverage = RequirementCoverage(requirement_id=999, coverage_type="covered_by_story", story_ids=["s1"], acceptance_criteria_ids=[])

    state["classified_requirements"] = [req]
    state["user_stories"] = [story]
    state["requirement_coverages"] = [coverage]

    result = await quality_gate_node(state)

    # Should produce high severity for empty requirement text and missing evidence
    assert any(q.severity == "high" for q in result["quality_issues"]) is True
    # status should be needs_review when high severity present
    assert result["status"] == "needs_review"
