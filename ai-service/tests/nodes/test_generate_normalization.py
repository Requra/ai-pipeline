import pytest
from app.nodes.generate import normalize_generation_payload, GenerationResponse, generate_node
from app.schemas.items import ClassifiedRequirement, EvidenceSpan
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
async def test_generation_repairs_truncated_json_once_before_fallback(base_state):
    """A malformed provider response gets one repair opportunity, not fallback."""
    state = base_state.copy()
    state["job_id"] = "json-repair"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Administrators shall register assets.",
            actor="administrator",
            goal="register assets",
            candidate_labels=["FR"],
            labels=["FR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[],
        )
    ]
    repaired = json.dumps({
        "stories": [{
            "source_requirement_id": 1,
            "title": "Register assets",
            "description": "As an administrator, I want to register assets, so that assets are recorded.",
            "acceptance_criteria": [
                "Given an administrator, when an asset is registered, then the asset is recorded."
            ],
            "labels": ["FR"],
        }]
    })
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=[
        '{"stories": [{"source_requirement_id": 1, "title": "Register',
        MagicMock(content=repaired),
    ])

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    assert mock_llm.ainvoke.await_count == 2
    assert len(result["user_stories"]) == 1
    assert not any(
        warning["code"] == "GENERATE_LLM_FAILURE_FALLBACK"
        for warning in result.get("warnings", [])
    )


@pytest.mark.asyncio
async def test_full_generation_failure_recovers_in_small_batch_without_fallback(base_state):
    """A large-response failure must not degrade stories when its batch recovers."""
    state = base_state.copy()
    state["job_id"] = "batch-recovery"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Administrators shall register assets.",
            actor="administrator", goal="register assets",
            candidate_labels=["FR"], labels=["FR"], confidence=1.0,
            classification_confidence=1.0, evidence=[],
        ),
        ClassifiedRequirement(
            id=2,
            text="Users shall request asset checkout.",
            actor="user", goal="request asset checkout",
            candidate_labels=["FR"], labels=["FR"], confidence=1.0,
            classification_confidence=1.0, evidence=[],
        ),
    ]
    recovered = json.dumps({"stories": [
        {"source_requirement_id": 1, "title": "Register assets", "description": "As an administrator, I want to register assets, so that assets are recorded.", "acceptance_criteria": ["Given an administrator, when an asset is registered, then the asset is recorded."], "labels": ["FR"]},
        {"source_requirement_id": 2, "title": "Request asset checkout", "description": "As a user, I want to request asset checkout, so that checkout can be considered.", "acceptance_criteria": ["Given a user, when checkout is requested, then the request is recorded."], "labels": ["FR"]},
    ]})
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=[
        RuntimeError("full response truncated"),
        MagicMock(content=recovered),
    ])

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    assert len(result["user_stories"]) == 2
    assert result.get("status") != "partial"
    assert not any(
        issue.rule_violated == "generation_degraded"
        for issue in result.get("quality_issues", [])
    )


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
    # A model-proposed N:1 merge is expanded because the public contract and
    # exports expose a single primary requirement ID per story.
    assert len(stories) == 2
    assert {tuple(story.source_requirement_ids) for story in stories} == {(1,), (2,)}
    assert {story.source_requirement_ids[0]: story.priority for story in stories} == {
        1: "Critical",
        2: "Low",
    }


@pytest.mark.asyncio
async def test_generate_removes_unsupported_acceptance_facts(base_state):
    state = base_state.copy()
    state["job_id"] = "fact-ledger"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The owner shall invite named collaborators to a project.",
            actor="owner", goal="invite named collaborators",
            candidate_labels=["FR"], labels=["FR"], confidence=1.0,
            classification_confidence=1.0, evidence=[], priority="Medium",
        )
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
        "stories": [{
            "source_requirement_ids": [1],
            "title": "Invite collaborators",
            "description": "As an owner, I want to invite named collaborators, so that they can join the project.",
            "acceptance_criteria": [
                "Given an invalid email, when an invitation is submitted, then an error is displayed.",
                "Given an owner, when collaborators are invited, then they join the project.",
                "Given an invitation, when it is submitted, then the system informs the owner.",
            ],
            "labels": ["FR"],
            "story_points": 13,
        }]
    })))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    story = result["user_stories"][0]
    combined = " ".join(criterion.text.lower() for criterion in story.acceptance_criteria)
    assert "invalid" not in combined
    assert "error" not in combined
    assert "informs" not in combined
    assert story.story_points in {1, 2, 3, 5, 8}
    assert len(story.acceptance_criteria) >= 1


@pytest.mark.asyncio
async def test_generate_audio_story_normalizes_transcript_grammar_and_rejects_unstated_lifecycle(base_state):
    """Audio-only guards must not publish ASR grammar or invented lifecycle states."""
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text=(
                "Asset database records cannot be permanently deleted and must be "
                "soft-deleted and marked as Retired."
            ),
            actor="System", goal="soft-delete and mark records as Retired",
            candidate_labels=["BR"], labels=["BR"], confidence=0.9,
            classification_confidence=0.9,
            evidence=[EvidenceSpan(
                chunk_id="trans_audio_semantic_0",
                quote=(
                    "Asset database records cannot be permanently deleted; they must be "
                    "soft deleted and marked as Retired."
                ),
                timestamp="14.0",
            )],
        )
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
        "stories": [{
            "source_requirement_ids": [1],
            "title": "Softs delete retired records",
            "description": (
                "As a system operator, I want to softs delete records, so that "
                "they become inactive or archived."
            ),
            "acceptance_criteria": [
                "Given an asset record, when it is retired, then the system softs delete it and archives it."
            ],
            "labels": ["BR"], "story_points": 3,
        }]
    })))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    story = result["user_stories"][0]
    published = " ".join([
        story.title, story.description,
        *[criterion.text for criterion in story.acceptance_criteria],
    ]).lower()
    assert "softs delete" not in published
    assert "archive" not in published
    assert "inactive" not in published
    assert "soft-delete" in published


@pytest.mark.asyncio
async def test_generate_replaces_unsupported_title_and_limit_outcome(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Users shall be allowed to check out up to 3 assets simultaneously.",
            actor="User", goal="Check out up to 3 assets",
            candidate_labels=["BR"], labels=["BR"], confidence=0.9,
            classification_confidence=0.9, evidence=[], priority="Medium",
        )
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
        "stories": [{
            "source_requirement_ids": [1],
            "title": "Unlimited asset checkout and notifications",
            "description": (
                "As a user, I want assets without restrictions, so that I can "
                "obtain everything I need."
            ),
            "acceptance_criteria": [
                "Given fewer than 3 checked-out assets, when another is requested, then the system allows checkout.",
                "Given 3 checked-out assets, when another is requested, then the system prevents the additional checkout."
            ],
            "labels": ["BR"], "story_points": 3,
        }]
    })))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    story = result["user_stories"][0]
    published = " ".join([
        story.title,
        story.description,
        *[criterion.text for criterion in story.acceptance_criteria],
    ]).lower()
    assert "unlimited" not in published
    assert "without restrictions" not in published
    assert "inform" not in published
    assert "up to 3" in published
    assert "prevents the additional checkout" in published
    assert len(story.acceptance_criteria) == 2


@pytest.mark.asyncio
async def test_generate_restores_omitted_measurable_constraints_in_criteria(base_state):
    requirement_text = (
        "The dashboard shall load and become interactive in less than 2.0 seconds "
        "under normal concurrent load of up to 500 active sessions."
    )
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1, text=requirement_text, actor="System", goal="Load dashboard",
            candidate_labels=["NFR"], labels=["NFR"], confidence=0.9,
            classification_confidence=0.9, evidence=[], priority="Medium",
        )
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
        "stories": [{
            "source_requirement_ids": [1],
            "title": "Load dashboard",
            "description": "As a system operator, I want to load the dashboard, so that it is responsive.",
            "acceptance_criteria": [
                "Given normal load, when users open the dashboard, then it loads within the specified time."
            ],
            "labels": ["NFR"], "story_points": 5,
        }]
    })))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    story = result["user_stories"][0]
    criteria = " ".join(item.text for item in story.acceptance_criteria).lower()
    assert "2.0 seconds" in criteria
    assert "500 active sessions" in criteria
    assert "does not proceed" not in criteria


@pytest.mark.asyncio
async def test_generate_removes_unsupported_vague_performance_criterion(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The dashboard shall load in less than 2 seconds under 500 active sessions.",
            actor="System", goal="provide a responsive dashboard",
            candidate_labels=["NFR"], labels=["NFR"], confidence=0.9,
            classification_confidence=0.9, evidence=[], priority="Medium",
        )
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
        "stories": [{
            "source_requirement_ids": [1],
            "title": "Responsive dashboard",
            "description": "As a system operator, I want a responsive dashboard, so that the documented requirement is fulfilled.",
            "acceptance_criteria": [
                "Given a user is on the dashboard, when they perform an action, then it responds in a timely manner."
            ],
            "labels": ["NFR"], "story_points": 3,
        }]
    })))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    story = result["user_stories"][0]
    published = " ".join(criterion.text for criterion in story.acceptance_criteria).lower()
    assert "timely manner" not in published
    assert "2 seconds" in published
    assert "500 active sessions" in published


@pytest.mark.asyncio
async def test_generate_does_not_turn_soft_delete_into_an_authorized_delete(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text=(
                "Asset database records shall not be permanently deleted; instead, "
                "they must be soft-deleted and marked as Retired."
            ),
            actor="User", goal="manage asset database records",
            candidate_labels=["BR"], labels=["BR"], confidence=0.9,
            classification_confidence=0.9, evidence=[], priority="Medium",
        )
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
        "stories": [{
            "source_requirement_ids": [1],
            "title": "Manage records",
            "description": "As a system operator, I want to manage records, so that the documented requirement is fulfilled.",
            "acceptance_criteria": [
                "Given an asset record, when the record is deleted, then it is soft-deleted and marked as Retired."
            ],
            "labels": ["BR"], "story_points": 3,
        }]
    })))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    story = result["user_stories"][0]
    criteria = " ".join(criterion.text for criterion in story.acceptance_criteria).lower()
    assert "when the record is deleted" not in criteria
    assert "not permanently delete" in criteria
    assert story.description.lower().startswith("as a user,")


@pytest.mark.asyncio
async def test_generate_rejects_unrelated_declared_mapping(base_state):
    state = base_state.copy()
    state["job_id"] = "mapping-ledger"
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The application shall retain exported reports for thirty days.",
            actor="administrator", goal="retrieve retained reports",
            candidate_labels=["FR"], labels=["FR"], confidence=1.0,
            classification_confidence=1.0, evidence=[], priority="Medium",
        )
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
        "stories": [{
            "source_requirement_ids": [1],
            "title": "Notify owners",
            "description": "As an owner, I want administrator role notifications, so that I stay informed.",
            "acceptance_criteria": ["Given a role change, when it happens, then an owner is notified."],
            "labels": ["FR"], "story_points": 3,
        }]
    })))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    story = result["user_stories"][0]
    assert story.source_requirement_ids == [1]
    assert "retains exported reports" in " ".join(
        criterion.text.lower() for criterion in story.acceptance_criteria
    )

