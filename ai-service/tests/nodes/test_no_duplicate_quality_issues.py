import pytest
from app.nodes.evidence_grounding import evidence_grounding_node
from app.nodes.quality_gate import quality_gate_node
from app.schemas.items import QualityIssue, ClassifiedRequirement, EvidenceSpan, SourceChunk, UserStory, RequirementCoverage


@pytest.mark.asyncio
async def test_quality_issues_not_duplicated(base_state):
    state = base_state.copy()
    # existing quality issue
    existing = QualityIssue(item_id=42, item_type="requirement", severity="medium", rule_violated="foo", details="existing")
    state["quality_issues"] = [existing]

    # classified req missing evidence -> evidence_grounding will create one new high severity
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="No evidence",
            candidate_labels=["FR"],
            confidence=0.5,
            evidence=[],
            labels=["FR"],
            classification_confidence=0.5
        )
    ]

    state["chunks"] = [SourceChunk(chunk_id="c1", text="irrelevant", start_char=0, end_char=8)]

    # run grounding -> returns new issues only
    res1 = await evidence_grounding_node(state)
    new_issues_from_grounding = res1.get("quality_issues", [])
    assert len(new_issues_from_grounding) == 1

    # simulate pipeline reducer appending
    state["quality_issues"] = state["quality_issues"] + new_issues_from_grounding

    # run quality gate which should return only additional new issues (if any)
    res2 = await quality_gate_node(state)
    new_issues_from_gate = res2.get("quality_issues", [])

    # final list should be existing + grounding + gate (gate should not duplicate grounding)
    final_combined = state["quality_issues"] + new_issues_from_gate
    # ensure no duplicates by object identity or same fields
    seen = set()
    for q in final_combined:
        key = (q.item_id, q.item_type, q.rule_violated, q.details)
        seen.add(key)

    assert len(final_combined) == len(seen)
