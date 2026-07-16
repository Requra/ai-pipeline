"""Phase 8 — summarize grounded in extracted artifacts."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.nodes.summarize import _build_artifact_digest, summarize_node
from app.schemas.items import ClassifiedRequirement, EvidenceSpan, UserStory


def _req(rid, text, labels):
    return ClassifiedRequirement(
        id=rid, text=text, candidate_labels=labels, labels=labels,
        confidence=0.9, classification_confidence=0.9,
        evidence=[EvidenceSpan(chunk_id="c", quote="q")],
    )


def _story(sid, title):
    return UserStory(id=sid, title=title, description="As a user, I want X, so that Y.",
                     acceptance_criteria=[], source_requirement_ids=[1], labels=["FR"])


def test_digest_includes_requirements_stories_and_open_questions():
    state = {
        "classified_requirements": [
            _req(1, "The system shall export invoices.", ["FR"]),
            _req(2, "Should we support multi-currency?", ["Open Question"]),
        ],
        "user_stories": [_story("s1", "Export invoices")],
    }
    digest = _build_artifact_digest(state)
    assert "export invoices" in digest.lower()
    assert "Export invoices" in digest          # story title
    assert "Open questions" in digest
    assert "multi-currency" in digest.lower()


def test_digest_empty_when_no_artifacts():
    assert _build_artifact_digest({"classified_requirements": [], "user_stories": []}) == ""


@pytest.mark.asyncio
async def test_summarize_uses_artifacts_in_user_message():
    state = {
        "job_id": "sum-1",
        "raw_text": "Project brief about an invoicing system.",
        "classified_requirements": [_req(1, "The system shall export invoices to PDF.", ["FR"])],
        "user_stories": [_story("s1", "Export invoices to PDF")],
    }
    captured = {}

    async def fake_ainvoke(messages, **kwargs):
        captured["user"] = messages[1][1]
        return MagicMock(content="Executive summary: invoicing system with PDF export.")

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=fake_ainvoke)
    with patch("app.nodes.summarize.get_llm", return_value=mock_llm):
        result = await summarize_node(state)

    # The structured digest reached the LLM, and a summary came back.
    assert "Structured analysis" in captured["user"]
    assert "export invoices to pdf" in captured["user"].lower()
    assert result["summary"].executive_summary
