"""Phase 6 — generation quality: specific fallback ACs, validation warnings."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.nodes.generate import build_specific_acceptance_criteria, generate_node
from app.schemas.items import ClassifiedRequirement, EvidenceSpan
from app.validators.story_validator import is_generic_ac


def _req(rid, text, labels, **over):
    base = dict(id=rid, text=text, candidate_labels=labels, labels=labels,
                confidence=1.0, classification_confidence=1.0,
                evidence=[EvidenceSpan(chunk_id=f"c{rid}", quote=text[:10])])
    base.update(over)
    return ClassifiedRequirement(**base)


def test_fallback_criteria_are_specific_and_at_least_two():
    req = _req(1, "The system shall export invoices to PDF.", ["FR"], actor="user", goal="export invoices")
    acs = build_specific_acceptance_criteria(req, "job_story_1", "a user")
    assert len(acs) >= 2
    assert acs[0].id == "job_story_1_ac_1"
    # None are generic boilerplate, and they reference the requirement content.
    assert not any(is_generic_ac(ac.text) for ac in acs)
    assert any("invoices to PDF" in ac.text for ac in acs)


def test_nfr_fallback_criteria_mention_measurement():
    req = _req(2, "Responses must complete within 500ms.", ["NFR"])
    acs = build_specific_acceptance_criteria(req, "job_story_2", "a user")
    assert len(acs) >= 2
    assert any("500ms" in ac.text for ac in acs)


@pytest.mark.asyncio
async def test_skipped_requirement_gets_specific_fallback_story(base_state):
    """LLM skips a requirement -> fallback story with >=2 specific ACs (no generic)."""
    state = base_state.copy()
    state["job_id"] = "q-job"
    state["classified_requirements"] = [
        _req(1, "The system shall allow exporting reports to CSV.", ["FR"], actor="analyst", goal="export reports"),
    ]
    # LLM returns NO story for requirement 1 -> fallback path.
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": []})))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    stories = result["user_stories"]
    assert len(stories) == 1
    story = stories[0]
    assert len(story.acceptance_criteria) >= 2
    assert not any(is_generic_ac(ac.text) for ac in story.acceptance_criteria)
    # The old generic boilerplate must be gone.
    assert all("implemented as specified" not in ac.text.lower() for ac in story.acceptance_criteria)
    # Coverage preserved.
    assert any(c.requirement_id == 1 for c in result["requirement_coverages"])


@pytest.mark.asyncio
async def test_total_fallback_produces_specific_criteria(base_state):
    """LLM raises -> total fallback path still yields >=2 specific ACs."""
    state = base_state.copy()
    state["job_id"] = "q-job2"
    state["classified_requirements"] = [
        _req(1, "Users can reset their password by email.", ["FR"], actor="user", goal="reset password"),
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("boom"))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    assert result["status"] == "partial"
    story = result["user_stories"][0]
    assert len(story.acceptance_criteria) >= 2
    assert not any(is_generic_ac(ac.text) for ac in story.acceptance_criteria)


@pytest.mark.asyncio
async def test_short_llm_acceptance_criteria_are_replaced_before_quality_gate(base_state):
    state = base_state.copy()
    state["job_id"] = "q-job3"
    state["classified_requirements"] = [_req(1, "The system shall send emails.", ["FR"])]
    # LLM returns one criterion; source-bound sanitization replaces it.
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": [
        {"source_requirement_id": 1, "title": "Send emails",
         "description": "As a user, I want to send emails, so that I can communicate.",
         "acceptance_criteria": ["Given a user, when they send, then it is delivered."], "labels": ["FR"]},
    ]})))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    assert not any(w["code"] == "GENERATE_STORY_QUALITY" for w in result.get("warnings", []))
    assert len(result["user_stories"][0].acceptance_criteria) >= 2
    assert all("send emails" in ac.text.lower() for ac in result["user_stories"][0].acceptance_criteria)
