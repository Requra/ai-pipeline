from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json

from app.nodes.repair_stories import repair_stories_node
from app.graph.router import route_after_quality_gate
from app.config import settings
from app.schemas.items import UserStory, AcceptanceCriterion, QualityIssue, ClassifiedRequirement


def _story(sid, title="title", description="As a user, I want X, so that Y.", acs=None):
    if acs is None:
        acs = [AcceptanceCriterion(id=f"{sid}_ac_1", text="Given Z, when W, then V.")]
    return UserStory(
        id=sid,
        title=title,
        description=description,
        acceptance_criteria=acs,
        source_requirement_ids=[1],
        labels=["FR"]
    )


def _issue(rule, details, item_id=0):
    return QualityIssue(
        item_id=item_id,
        item_type="story",
        severity="medium",
        rule_violated=rule,
        details=details
    )


@pytest.fixture
def base_state_repair():
    import time
    return {
        "job_id": "test-job-123",
        "raw_bytes": b"",
        "raw_text": None,
        "file_type": "text",
        "metadata": {},
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [
            ClassifiedRequirement(
                id=1,
                text="The system shall do X.",
                labels=["FR"],
                classification_confidence=1.0,
                confidence=1.0,
                evidence=[]
            )
        ],
        "user_stories": [],
        "quality_issues": [],
        "resolved_quality_issues": [],
        "warnings": [],
        "status": "started",
        "error": None,
        "started_at": time.time(),
        "processing_time_ms": 0,
        "repair_attempts": 0
    }


def test_repair_skipped_when_disabled(base_state_repair):
    state = base_state_repair.copy()
    state["user_stories"] = [_story("story_1")]
    state["quality_issues"] = [_issue("story_description_shape", "Story story_1 is wrong")]
    
    with patch.object(settings, "ENABLE_QUALITY_REPAIR", False):
        route = route_after_quality_gate(state)
        assert route == "summarize"


def test_repair_skipped_when_no_repairable_issues(base_state_repair):
    state = base_state_repair.copy()
    state["user_stories"] = [_story("story_1")]
    # semantic_conflict_contradiction is NOT in repairable whitelist
    state["quality_issues"] = [_issue("semantic_conflict_contradiction", "Story story_1 has conflict")]
    
    with patch.object(settings, "ENABLE_QUALITY_REPAIR", True):
        route = route_after_quality_gate(state)
        assert route == "summarize"


def test_repair_triggered_for_repairable_issues(base_state_repair):
    state = base_state_repair.copy()
    state["user_stories"] = [_story("story_1")]
    state["quality_issues"] = [_issue("story_description_shape", "Story story_1 shape invalid")]
    
    with patch.object(settings, "ENABLE_QUALITY_REPAIR", True):
        route = route_after_quality_gate(state)
        assert route == "repair_stories"


def test_repair_max_attempts_respected(base_state_repair):
    state = base_state_repair.copy()
    state["user_stories"] = [_story("story_1")]
    state["quality_issues"] = [_issue("story_description_shape", "Story story_1 shape invalid")]
    state["repair_attempts"] = 1
    
    with patch.object(settings, "ENABLE_QUALITY_REPAIR", True), \
         patch.object(settings, "MAX_REPAIR_ATTEMPTS", 1):
        route = route_after_quality_gate(state)
        assert route == "summarize"


def test_repair_story_must_exist(base_state_repair):
    state = base_state_repair.copy()
    # Story story_1 does NOT exist in state["user_stories"]
    state["user_stories"] = []
    state["quality_issues"] = [_issue("story_description_shape", "Story story_1 shape invalid")]
    
    with patch.object(settings, "ENABLE_QUALITY_REPAIR", True):
        route = route_after_quality_gate(state)
        assert route == "summarize"


@pytest.mark.asyncio
async def test_repair_replaces_only_failed_stories(base_state_repair):
    s1 = _story("story_1")
    s2 = _story("story_2")
    s3 = _story("story_3")
    
    state = base_state_repair.copy()
    state["user_stories"] = [s1, s2, s3]
    state["quality_issues"] = [_issue("story_missing_acceptance", "Story story_2 missing AC")]
    
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "stories": [
            {
                "id": "story_2",
                "title": "repaired title",
                "description": "As a user, I want Y, so that Z.",
                "acceptance_criteria": ["Given X, when Y, then Z.", "Given A, when B, then C."],
                "labels": ["FR"]
            }
        ]
    })
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    with patch("app.nodes.repair_stories.get_llm", return_value=mock_llm):
        out = await repair_stories_node(state)
        
        updated = out["user_stories"]
        assert len(updated) == 3
        
        # Verify s1 and s3 are EXACTLY the same objects (same memory reference)
        assert updated[0] is s1
        assert updated[2] is s3
        
        # Verify s2 was replaced/repaired
        assert updated[1] is not s2
        assert updated[1].id == "story_2"
        assert updated[1].title == "repaired title"
        assert len(updated[1].acceptance_criteria) == 1
        coverage = out["requirement_coverages"][0]
        covered_story = next(story for story in updated if story.id in coverage.story_ids)
        assert coverage.acceptance_criteria_ids == [
            criterion.id for criterion in covered_story.acceptance_criteria
        ]


@pytest.mark.asyncio
async def test_repair_batches_into_single_llm_call(base_state_repair):
    s1 = _story("story_1")
    s2 = _story("story_2")
    
    state = base_state_repair.copy()
    state["user_stories"] = [s1, s2]
    state["quality_issues"] = [
        _issue("story_missing_acceptance", "Story story_1 missing AC"),
        _issue("story_description_shape", "Story story_2 bad shape")
    ]
    
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "stories": [
            {
                "id": "story_1",
                "title": "rep 1",
                "description": "As a user...",
                "acceptance_criteria": ["Given AC1", "Given AC2"],
                "labels": ["FR"]
            },
            {
                "id": "story_2",
                "title": "rep 2",
                "description": "As a user...",
                "acceptance_criteria": ["Given AC3", "Given AC4"],
                "labels": ["FR"]
            }
        ]
    })
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    with patch("app.nodes.repair_stories.get_llm", return_value=mock_llm):
        await repair_stories_node(state)
        # Verify ainvoke was called EXACTLY once (single batch)
        assert mock_llm.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_repair_preserves_resolved_issues(base_state_repair):
    s1 = _story("story_1")
    state = base_state_repair.copy()
    state["user_stories"] = [s1]
    # One repairable issue and one non-repairable issue
    state["quality_issues"] = [
        _issue("story_missing_acceptance", "Story story_1 missing AC"),
        _issue("semantic_conflict_contradiction", "Story story_1 has conflict")
    ]
    
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "stories": [
            {
                "id": "story_1",
                "title": "rep 1",
                "description": "As a user...",
                "acceptance_criteria": ["Given AC1", "Given AC2"],
                "labels": ["FR"]
            }
        ]
    })
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    with patch("app.nodes.repair_stories.get_llm", return_value=mock_llm):
        out = await repair_stories_node(state)
        
        # Repairable was moved to resolved_quality_issues
        assert len(out["resolved_quality_issues"]) == 1
        assert out["resolved_quality_issues"][0].rule_violated == "story_missing_acceptance"
        
        # Non-repairable remains in quality_issues
        assert len(out["quality_issues"]) == 1
        assert out["quality_issues"][0].rule_violated == "semantic_conflict_contradiction"
        
        assert out["repair_attempts"] == 1


@pytest.mark.asyncio
async def test_repair_cannot_reintroduce_persona_or_duplicate_criteria(base_state_repair):
    source = "The system shall generate a unique QR code for every registered asset."
    state = base_state_repair.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text=source,
            actor="System",
            goal="generate a unique QR code",
            labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[],
        )
    ]
    story = _story(
        "story_1",
        description=(
            "As the system, I want to generate a unique QR code for every asset, "
            "so that each asset is identifiable."
        ),
    )
    state["user_stories"] = [story]
    state["quality_issues"] = [
        _issue("non_human_story_persona", "Story story_1 uses a technical component.")
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": [{
        "id": "story_1",
        "title": "Generate asset QR code",
        "description": (
            "As the system, I want to generate a unique QR code for every asset, "
            "so that each asset is identifiable."
        ),
        "acceptance_criteria": [
            "Given an asset, when its QR code is generated, then the code is unique.",
            "Given two assets, when QR codes are generated, then the codes are distinct.",
        ],
        "labels": ["FR"],
    }]})))

    with patch("app.nodes.repair_stories.get_llm", return_value=mock_llm):
        out = await repair_stories_node(state)

    repaired = out["user_stories"][0]
    assert repaired.description.startswith("As a system operator,")
    assert len(repaired.acceptance_criteria) == 1
    assert out["requirement_coverages"][0].acceptance_criteria_ids == [
        repaired.acceptance_criteria[0].id
    ]
