import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from app.nodes.generate import generate_node
from app.schemas.items import ClassifiedRequirement, EvidenceSpan

@pytest.mark.asyncio
async def test_generate_node_robust_mapping(base_state):
    """
    Test that mapping is deterministic even if LLM output is messy.
    Cases:
    - LLM renumbers its internal IDs (we should ignore them and use source_requirement_id)
    - LLM duplicates a requirement (we should take the first one)
    - LLM skips a requirement (we should generate a fallback)
    """
    state = base_state.copy()
    state["job_id"] = "robust-job"
    
    ev1 = EvidenceSpan(chunk_id="c1", quote="Q1")
    ev2 = EvidenceSpan(chunk_id="c2", quote="Q2")
    ev3 = EvidenceSpan(chunk_id="c3", quote="Q3")

    state["classified_requirements"] = [
        ClassifiedRequirement(id=10, text="Req 1", labels=["FR"], confidence=1.0, evidence=[ev1]),
        ClassifiedRequirement(id=20, text="Req 2", labels=["FR"], confidence=1.0, evidence=[ev2]),
        ClassifiedRequirement(id=30, text="Req 3", labels=["FR"], confidence=1.0, evidence=[ev3]),
    ]

    mock_llm = MagicMock()
    # Messy LLM output:
    # - returns story for 10 (renumbered as 1 internally)
    # - duplicates story for 10 (renumbered as 2)
    # - skips 20 entirely
    # - returns story for 30 (renumbered as 3)
    mock_resp = json.dumps({
        "stories": [
            {
                "source_requirement_id": 10,
                "title": "T1", "description": "As a user, I want T1", 
                "acceptance_criteria": ["AC1"], "labels": ["FR"]
            },
            {
                "source_requirement_id": 10, # Duplicate
                "title": "T1-Dup", "description": "As a user, I want T1-Dup", 
                "acceptance_criteria": ["AC1-Dup"], "labels": ["FR"]
            },
            # 20 is missing
            {
                "source_requirement_id": 30,
                "title": "T3", "description": "As a user, I want T3", 
                "acceptance_criteria": ["AC3"], "labels": ["FR"]
            }
        ]
    })
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    stories = result["user_stories"]
    coverages = result["requirement_coverages"]

    assert len(stories) == 3 # 10 (matched), 20 (fallback), 30 (matched)
    
    # Story for 10
    s10 = next(s for s in stories if s.source_requirement_ids == [10])
    assert s10.title == "T1" # First one kept
    assert s10.evidence_reference[0].chunk_id == "c1"
    assert s10.acceptance_criteria[0].id == "robust-job_story_10_ac_1"

    # Story for 20 (Fallback)
    s20 = next(s for s in stories if s.source_requirement_ids == [20])
    assert "fallback" in s20.title or "Req 2" in s20.title or "requirement 20" in s20.title
    assert "As a user, I want satisfy this requirement" in s20.description
    assert s20.evidence_reference[0].chunk_id == "c2"
    assert s20.acceptance_criteria[0].id == "robust-job_story_20_ac_1"

    # Story for 30
    s30 = next(s for s in stories if s.source_requirement_ids == [30])
    assert s30.title == "T3"
    assert s30.evidence_reference[0].chunk_id == "c3"

    # Coverage verification
    assert len(coverages) == 3
    assert all(c.coverage_type == "covered_by_story" for c in coverages)
    c10 = next(c for c in coverages if c.requirement_id == 10)
    assert c10.story_ids == ["robust-job_story_10"]
    assert c10.acceptance_criteria_ids == ["robust-job_story_10_ac_1", "robust-job_story_10_ac_2"]

@pytest.mark.asyncio
async def test_non_story_filtering_both_label_sources(base_state):
    """Verify non-story filtering checks both labels and candidate_labels."""
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1, text="Q", labels=["Open Question"], candidate_labels=[],
            confidence=1.0, evidence=[]
        ),
        ClassifiedRequirement(
            id=2, text="S", labels=["FR"], candidate_labels=["Out-of-Scope"],
            confidence=1.0, evidence=[]
        ),
        ClassifiedRequirement(
            id=3, text="R", labels=["FR"], candidate_labels=["FR"],
            confidence=1.0, evidence=[]
        )
    ]

    mock_llm = MagicMock()
    mock_resp = json.dumps({"stories": [
        {"source_requirement_id": 3, "title": "T", "description": "As...", "acceptance_criteria": ["AC"], "labels": ["FR"]}
    ]})
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    stories = result["user_stories"]
    coverages = result["requirement_coverages"]

    assert len(stories) == 1
    assert stories[0].source_requirement_ids == [3]
    
    assert len(coverages) == 3
    c1 = next(c for c in coverages if c.requirement_id == 1)
    assert c1.coverage_type == "non_story"
    c2 = next(c for c in coverages if c.requirement_id == 2)
    assert c2.coverage_type == "non_story"
