import pytest
from app.nodes.evidence_grounding import evidence_grounding_node
from app.schemas.items import ClassifiedRequirement, EvidenceSpan, SourceChunk


@pytest.mark.asyncio
async def test_evidence_grounding_passes_when_quote_in_chunk(base_state):
    state = base_state.copy()
    state["chunks"] = [SourceChunk(chunk_id="c1", text="The system shall process payments.", start_char=0, end_char=30)]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Process payments",
            actor="System",
            goal="process payments",
            candidate_labels=["FR"],
            confidence=0.9,
            evidence=[EvidenceSpan(chunk_id="c1", quote="process payments")],
            labels=["FR"],
            classification_confidence=0.9
        )
    ]

    result = await evidence_grounding_node(state)
    # Node returns only new quality_issues; classified_requirements are mutated in-place
    assert result["quality_issues"] == []
    cr = state["classified_requirements"][0]
    assert not cr.needs_review


@pytest.mark.asyncio
async def test_evidence_grounding_flags_missing_evidence(base_state):
    state = base_state.copy()
    state["chunks"] = [SourceChunk(chunk_id="c1", text="Some content here.", start_char=0, end_char=18)]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=2,
            text="A requirement with no evidence",
            candidate_labels=["FR"],
            confidence=0.5,
            evidence=[],
            labels=["FR"],
            classification_confidence=0.5
        )
    ]

    result = await evidence_grounding_node(state)
    assert any(q.rule_violated == "missing_evidence" for q in result["quality_issues"])
    cr = state["classified_requirements"][0]
    assert cr.needs_review is True


@pytest.mark.asyncio
async def test_evidence_grounding_flags_quote_not_found(base_state):
    state = base_state.copy()
    state["chunks"] = [SourceChunk(chunk_id="c1", text="Completely different text.", start_char=0, end_char=25)]
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=3,
            text="Requirement with quote not in chunks",
            candidate_labels=["FR"],
            confidence=0.6,
            evidence=[EvidenceSpan(chunk_id="c1", quote="not present quote")],
            labels=["FR"],
            classification_confidence=0.6
        )
    ]

    result = await evidence_grounding_node(state)
    assert any(q.rule_violated == "evidence_not_grounded" for q in result["quality_issues"])
    cr = state["classified_requirements"][0]
    assert cr.needs_review is True
