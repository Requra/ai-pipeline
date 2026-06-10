import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from app.nodes.generate import generate_node
from app.schemas.items import ClassifiedRequirement


def _format_story(s):
    return (
        f"\n================ STORY {s.source_requirement_ids} ================\n"
        f"TITLE: {s.title}\n"
        f"DESCRIPTION: {s.description}\n"
        f"LABELS: {s.labels}\n"
        f"ACCEPTANCE:\n" +
        "\n".join([f"  - {a.text}" for a in s.acceptance_criteria]) +
        "\n======================================================\n"
    )


@pytest.mark.asyncio
async def test_generate_node_multilabel_support(base_state, capsys):

    state = base_state.copy()
    state["job_id"] = "test-job"

    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The user shall log in within 0.1 seconds with secure authentication.",
            actor="User",
            goal="fast secure login",
            candidate_labels=["FR", "NFR"],
            labels=["FR", "NFR"],
            confidence=0.95,
            classification_confidence=0.95,
            evidence=[]
        ),
        ClassifiedRequirement(
            id=2,
            text="Only admins can delete user accounts.",
            actor="Admin",
            goal="user management",
            candidate_labels=["BR"],
            labels=["BR"],
            confidence=1.0,
            classification_confidence=1.0,
            evidence=[]
        )
    ]

    mock_resp = json.dumps({
        "stories": [
            {
                "source_requirement_id": 1,
                "title": "Fast secure login",
                "description": "As a User, I want to log in within 0.1s so that it's fast.",
                "acceptance_criteria": ["Log in < 0.1s"],
                "labels": ["FR", "NFR"]
            },
            {
                "source_requirement_id": 2,
                "title": "Admin account deletion",
                "description": "As an Admin, I want to delete accounts.",
                "acceptance_criteria": ["Only admins delete"],
                "labels": ["BR"]
            }
        ]
    })
    
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=mock_resp))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    assert "user_stories" in result
    stories = result["user_stories"]

    # ---------------- PRINT OUTPUT ----------------
    print("\n\n================ GENERATE NODE OUTPUT ================\n")
    for s in stories:
        print(_format_story(s))

    captured = capsys.readouterr()
    print(captured.out)  # ensures pytest shows it

    # ---------------- ASSERTIONS ----------------
    assert len(stories) == 2
    assert set(stories[0].labels) == {"FR", "NFR"}
    assert stories[1].labels == ["BR"]

    for s in stories:
        assert len(s.acceptance_criteria) > 0


@pytest.mark.asyncio
async def test_generate_node_empty(base_state):

    state = base_state.copy()
    state["classified_requirements"] = []

    result = await generate_node(state)

    print("\nEMPTY RESULT:", result)

    assert result["user_stories"] == []
