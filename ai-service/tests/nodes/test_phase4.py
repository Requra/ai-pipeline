import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from app.nodes.classify import classify_node
from app.nodes.generate import generate_node
from app.nodes.quality_gate import quality_gate_node
from app.schemas.items import (
    ExtractedRequirement, 
    ClassifiedRequirement, 
    SourceChunk, 
    UserStory, 
    AcceptanceCriterion, 
    RequirementCoverage,
    EvidenceSpan
)

@pytest.mark.asyncio
async def test_label_preservation_open_question(base_state):
    """Test A & B: Special labels are preserved from candidate_labels."""
    state = base_state.copy()
    state["extracted_requirements"] = [
        ExtractedRequirement(
            id=1,
            text="How is tax calculated?",
            candidate_labels=["Open Question"],
            confidence=1.0,
            evidence=[]
        ),
        ExtractedRequirement(
            id=2,
            text="Marketing site is out of scope.",
            candidate_labels=["Out-of-Scope"],
            confidence=1.0,
            evidence=[]
        ),
        ExtractedRequirement(
            id=3,
            text="Assume we use AWS.",
            candidate_labels=["Assumption"],
            confidence=1.0,
            evidence=[]
        )
    ]

    mock_llm = MagicMock()
    # Mock return value from classifier if it fails to recognize special labels
    mock_resp = json.dumps({"classifications": [
        {"id": 1, "labels": ["BR"], "confidence": 0.5},
        {"id": 2, "labels": ["BR"], "confidence": 0.5},
        {"id": 3, "labels": ["FR"], "confidence": 0.5}
    ]})
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.classify.get_llm", return_value=mock_llm):
        result = await classify_node(state)
    
    classified = result["classified_requirements"]
    assert classified[0].labels == ["Open Question"]
    assert classified[1].labels == ["Out-of-Scope"]
    assert classified[2].labels == ["Assumption"]
    assert all(c.classification_confidence == 0.9 for c in classified)

    # Verify quality_gate doesn't flag them
    state["classified_requirements"] = classified
    q_result = await quality_gate_node(state)
    issues = q_result["quality_issues"]
    assert not any(i.rule_violated == "requirement_missing_labels" for i in issues)


@pytest.mark.asyncio
async def test_non_story_generation(base_state):
    """Test C & D: Non-story types skip story generation and use 'non_story' coverage."""
    state = base_state.copy()
    state["job_id"] = "test"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Open question",
            labels=["Open Question"],
            candidate_labels=["Open Question"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        ),
        ClassifiedRequirement(
            id=2,
            text="Real requirement",
            labels=["FR"],
            candidate_labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        )
    ]

    mock_llm = MagicMock()
    mock_resp = json.dumps({"stories": [
        {"source_requirement_id": 2, "title": "T2", "description": "As a user, I want T2", "acceptance_criteria": ["C2"], "labels": ["FR"]}
    ]})
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)
    
    stories = result["user_stories"]
    coverages = result["requirement_coverages"]
    
    assert len(stories) == 1
    assert stories[0].source_requirement_ids == [2]
    
    assert len(coverages) == 2
    c1 = next(c for c in coverages if c.requirement_id == 1)
    assert c1.coverage_type == "non_story"
    # Ensure NO "non_story_requirement" string exists
    assert c1.coverage_type != "non_story_requirement"


@pytest.mark.asyncio
async def test_evidence_and_ac_ids(base_state):
    """Test E & F: Evidence copy and AC ID generation."""
    state = base_state.copy()
    state["job_id"] = "api-001"
    ev = EvidenceSpan(chunk_id="chk1", quote="Requirement text")
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The system shall save data.",
            labels=["FR"],
            candidate_labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[ev]
        )
    ]

    mock_llm = MagicMock()
    mock_resp = json.dumps({"stories": [
        {
            "source_requirement_id": 1, 
            "title": "Save data", 
            "description": "As a user...", 
            "acceptance_criteria": ["AC 1", "AC 2"], 
            "labels": ["FR"]
        }
    ]})
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)
    
    story = result["user_stories"][0]
    coverage = result["requirement_coverages"][0]
    
    # Check evidence copy
    assert len(story.evidence_reference) == 1
    assert story.evidence_reference[0].chunk_id == "chk1"
    
    # Check AC IDs
    assert story.acceptance_criteria[0].id == "api-001_story_1_ac_1"
    assert story.acceptance_criteria[1].id == "api-001_story_1_ac_2"
    
    # Check coverage links to AC IDs
    assert coverage.acceptance_criteria_ids == ["api-001_story_1_ac_1", "api-001_story_1_ac_2"]

@pytest.mark.asyncio
async def test_quality_gate_special_coverage(base_state):
    """Test 6: Quality gate flags Out-of-Scope covered by story."""
    state = base_state.copy()
    req = ClassifiedRequirement(
        id=1, text="Out of scope", labels=["Out-of-Scope"], candidate_labels=["Out-of-Scope"],
        confidence=1.0, classification_confidence=1.0, evidence=[]
    )
    state["classified_requirements"] = [req]
    state["user_stories"] = [UserStory(
        id="s1", title="S1", description="D1", acceptance_criteria=[], 
        source_requirement_ids=[1], labels=["BR"], evidence_reference=[]
    )]
    state["requirement_coverages"] = [RequirementCoverage(
        requirement_id=1, coverage_type="covered_by_story", story_ids=["s1"], 
        acceptance_criteria_ids=[], reason=None
    )]

    result = await quality_gate_node(state)
    issues = result["quality_issues"]
    
    assert any(i.rule_violated == "out_of_scope_covered_by_story" for i in issues)
    assert result["status"] == "needs_review"
