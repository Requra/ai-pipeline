import pytest
from app.nodes.generate import normalize_generation_payload, GenerationResponse, generate_node
from app.schemas.items import ClassifiedRequirement
from unittest.mock import MagicMock, patch, AsyncMock
import json

def test_normalize_generation_user_stories_key():
    payload = {
        "user_stories": [
            {
                "source_requirement_id": 1,
                "title": "T1",
                "description": "D1",
                "acceptance_criteria": ["C1"],
                "labels": ["FR"]
            }
        ]
    }
    normalized = normalize_generation_payload(payload)
    assert "stories" in normalized
    assert normalized["stories"][0]["title"] == "T1"
    GenerationResponse.model_validate(normalized)

def test_normalize_generation_direct_list():
    payload = [
        {
            "source_requirement_id": 2,
            "title": "T2",
            "description": "D2",
            "acceptance_criteria": ["C2"],
            "labels": ["NFR"]
        }
    ]
    normalized = normalize_generation_payload(payload)
    assert "stories" in normalized
    assert normalized["stories"][0]["title"] == "T2"
    GenerationResponse.model_validate(normalized)

def test_normalize_generation_stories_key():
    payload = {
        "stories": [
            {
                "source_requirement_id": 3,
                "title": "T3",
                "description": "D3",
                "acceptance_criteria": ["C3"],
                "labels": ["BR"]
            }
        ]
    }
    normalized = normalize_generation_payload(payload)
    assert "stories" in normalized
    GenerationResponse.model_validate(normalized)

def test_normalize_generation_items_key():
    payload = {
        "items": [
            {
                "source_requirement_id": 4,
                "title": "T4",
                "description": "D4",
                "acceptance_criteria": ["C4"],
                "labels": ["FR"]
            }
        ]
    }
    normalized = normalize_generation_payload(payload)
    assert "stories" in normalized
    GenerationResponse.model_validate(normalized)


@pytest.mark.asyncio
async def test_fallback_does_not_contain_error_message(base_state):
    state = base_state.copy()
    state["job_id"] = "job1"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The system shall do X.",
            actor=None,
            goal=None,
            candidate_labels=["FR"],
            labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        )
    ]

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("Simulated API failure"))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    assert "error_message" not in result
    assert result["status"] == "partial"
    assert "warnings" in result
    # Matches the new code GENERATE_LLM_FAILURE_FALLBACK
    assert any("GENERATE_LLM_FAILURE_FALLBACK" in w["code"] for w in result["warnings"])
    assert len(result["user_stories"]) == 1

@pytest.mark.asyncio
async def test_fallback_does_not_produce_as_a_none(base_state):
    state = base_state.copy()
    state["job_id"] = "job2"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Admins can configure settings.",
            actor=None,
            goal=None,
            candidate_labels=["FR"],
            labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        ),
        ClassifiedRequirement(
            id=2,
            text="Performance should be high.",
            actor=None,
            goal=None,
            candidate_labels=["NFR"],
            labels=["NFR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        )
    ]

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("Fail"))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)
        
    stories = result["user_stories"]
    assert len(stories) == 2
    
    # Check first story - should infer admin
    desc1 = stories[0].description.lower()
    assert "as a none" not in desc1
    assert "i want none" not in desc1
    assert "as an admin" in desc1
    assert "satisfy this requirement" in desc1
    
    # Check second story - should infer system for NFR
    desc2 = stories[1].description.lower()
    assert "as a none" not in desc2
    assert "as a system" in desc2

@pytest.mark.asyncio
async def test_generate_skips_non_actionable(base_state):
    state = base_state.copy()
    state["job_id"] = "job3"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="How does X work?",
            actor=None,
            goal=None,
            candidate_labels=["Open Question"],
            labels=["Open Question"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        ),
        ClassifiedRequirement(
            id=2,
            text="Feature Y is out of scope.",
            actor=None,
            goal=None,
            candidate_labels=["Out-of-Scope"],
            labels=["Out-of-Scope"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        ),
        ClassifiedRequirement(
            id=3,
            text="System shall do Z.",
            actor="User",
            goal="Do Z",
            candidate_labels=["FR"],
            labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        )
    ]

    mock_llm = MagicMock()
    mock_resp = json.dumps({
        "stories": [
            {
                "source_requirement_id": 3,
                "title": "Do Z",
                "description": "As a User, I want to Do Z.",
                "acceptance_criteria": ["C1"],
                "labels": ["FR"]
            }
        ]
    })
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    stories = result["user_stories"]
    coverages = result["requirement_coverages"]
    
    assert len(stories) == 1
    assert stories[0].source_requirement_ids == [3]
    
    assert len(coverages) == 3
    non_story_covs = [c for c in coverages if c.coverage_type == "non_story"]
    assert len(non_story_covs) == 2
    assert set(c.requirement_id for c in non_story_covs) == {1, 2}


@pytest.mark.asyncio
async def test_generate_story_priority_mapping(base_state):
    state = base_state.copy()
    state["job_id"] = "job_priority_test"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Critical performance rule.",
            actor="System",
            goal="Fast load time",
            candidate_labels=["NFR"],
            labels=["NFR"],
            confidence=1.0,
            classification_confidence=1.0,
            priority="Critical",
            evidence=[]
        ),
        ClassifiedRequirement(
            id=2,
            text="Optional color change.",
            actor="User",
            goal="Cool interface",
            candidate_labels=["FR"],
            labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            priority="Low",
            evidence=[]
        )
    ]

    mock_llm = MagicMock()
    # Mock LLM grouping both requirements into one story
    mock_resp = json.dumps({
        "stories": [
            {
                "source_requirement_ids": [1, 2],
                "title": "Performance and Theme UI",
                "description": "As a User, I want to experience fast loads and UI colors.",
                "acceptance_criteria": ["Given UI, when loaded, then load speed < 1s.", "Given UI, when rendered, then it uses configured theme colors."],
                "labels": ["FR"]
            }
        ]
    })
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    stories = result["user_stories"]
    assert len(stories) == 1
    # Priority should resolve to "Critical" (highest of "Critical" and "Low")
    assert stories[0].priority == "Critical"

