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


def test_fallback_criteria_are_specific_and_cover_atomic_requirement():
    req = _req(1, "The system shall export invoices to PDF.", ["FR"], actor="user", goal="export invoices")
    acs = build_specific_acceptance_criteria(req, "job_story_1", "a user")
    assert len(acs) >= 1
    assert acs[0].id == "job_story_1_ac_1"
    # None are generic boilerplate, and they reference the requirement content.
    assert not any(is_generic_ac(ac.text) for ac in acs)
    assert any("invoices to PDF" in ac.text for ac in acs)
    assert all("then The system shall" not in ac.text for ac in acs)


def test_nfr_fallback_criteria_mention_measurement():
    req = _req(2, "Responses must complete within 500ms.", ["NFR"])
    acs = build_specific_acceptance_criteria(req, "job_story_2", "a user")
    assert len(acs) >= 1
    assert any("500ms" in ac.text for ac in acs)


@pytest.mark.asyncio
async def test_skipped_requirement_gets_specific_fallback_story(base_state):
    """LLM skips a requirement -> fallback story with source-specific ACs."""
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
    assert len(story.acceptance_criteria) >= 1
    assert not any(is_generic_ac(ac.text) for ac in story.acceptance_criteria)
    # The old generic boilerplate must be gone.
    assert all("implemented as specified" not in ac.text.lower() for ac in story.acceptance_criteria)
    # Coverage preserved.
    assert any(c.requirement_id == 1 for c in result["requirement_coverages"])


@pytest.mark.asyncio
async def test_total_fallback_produces_specific_criteria(base_state):
    """LLM raises -> total fallback path still yields specific ACs."""
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
    assert len(story.acceptance_criteria) >= 1
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
    assert len(result["user_stories"][0].acceptance_criteria) >= 1
    assert all("sends emails" in ac.text.lower() for ac in result["user_stories"][0].acceptance_criteria)


@pytest.mark.asyncio
async def test_story_validation_issue_uses_numeric_index_without_fallback(base_state):
    state = base_state.copy()
    state["job_id"] = "validation-index"
    state["classified_requirements"] = [
        _req(1, "The system shall export invoices to PDF.", ["FR"], actor="user"),
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": [{
        "source_requirement_id": 1,
        "title": "Export invoices",
        "description": "As a user, I want to export invoices to PDF, so that I can share them.",
        "acceptance_criteria": [
            "Given an invoice, when it is exported, then the invoice is exported to PDF."
        ],
        "labels": ["FR"],
    }]})))

    with (
        patch("app.nodes.generate.get_llm", return_value=mock_llm),
        patch(
            "app.nodes.generate.validate_stories",
            return_value={"validation-index_story_1": ["duplicate_acceptance_criteria"]},
        ),
    ):
        result = await generate_node(state)

    assert not any(
        warning["code"] == "GENERATE_LLM_FAILURE_FALLBACK"
        for warning in result.get("warnings", [])
    )
    issue = next(issue for issue in result["quality_issues"] if issue.rule_violated == "duplicate_acceptance_criterion")
    assert issue.item_id == 1


@pytest.mark.asyncio
async def test_generation_rejects_approval_success_not_stated_in_source(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [
        _req(
            1,
            "Standard checkout requests require manager approval above $1,000.",
            ["BR"],
            actor="manager",
        ),
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": [{
        "source_requirement_id": 1,
        "title": "Approve high-value checkout",
        "description": "As a manager, I want to review high-value checkout requests, so that approval is required.",
        "acceptance_criteria": [
            "Given a request above $1,000, when a manager reviews it, then the request is approved."
        ],
        "labels": ["BR"],
    }]})))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    criteria = " ".join(
        criterion.text.lower()
        for criterion in result["user_stories"][0].acceptance_criteria
    )
    assert "request is approved" not in criteria
    assert "approval" in criteria


@pytest.mark.asyncio
async def test_generic_required_details_are_replaced_with_source_bound_criteria(base_state):
    state = base_state.copy()
    state["job_id"] = "detailed-audio-rule"
    source = (
        "Administrators shall register hardware assets with an asset name, serial number, "
        "purchase date, and initial department assignment."
    )
    state["classified_requirements"] = [_req(1, source, ["FR"])]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": [
        {"source_requirement_id": 1, "title": "Register assets",
         "description": "As an administrator, I want to register assets, so that they are tracked.",
         "acceptance_criteria": [
             "Given an administrator, when required asset details are submitted, then the asset is registered."
         ], "labels": ["FR"]},
    ]})))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    criterion = result["user_stories"][0].acceptance_criteria[0].text.lower()
    assert "asset name" in criterion
    assert "serial number" in criterion
    assert "purchase date" in criterion
    assert "initial department assignment" in criterion


@pytest.mark.asyncio
async def test_generation_removes_unsupported_and_duplicate_criteria(base_state):
    state = base_state.copy()
    state["job_id"] = "source-bound"
    source = "The system shall generate a unique QR code for every registered asset."
    state["classified_requirements"] = [_req(1, source, ["FR"], actor="system", goal="generate a unique QR code")]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": [{
        "source_requirement_id": 1,
        "title": "Generate asset QR code",
        "description": "As a system operator, I want to generate a unique QR code for every asset, so that each asset is identifiable.",
        "acceptance_criteria": [
            "Given a registered asset, when its QR code is generated, then the QR code is unique for that asset.",
            "Given multiple registered assets, when QR codes are generated, then every asset has a distinct QR code.",
            "Given an asset list, when it opens, then the QR code is displayed in the list.",
        ],
        "labels": ["FR"],
    }]})))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    criteria = result["user_stories"][0].acceptance_criteria
    assert len(criteria) == 1
    assert "displayed in the list" not in criteria[0].text.lower()


@pytest.mark.asyncio
async def test_generation_normalizes_technical_persona_before_quality_repair(base_state):
    state = base_state.copy()
    state["job_id"] = "persona"
    source = "The system shall generate a unique code for each record."
    state["classified_requirements"] = [
        _req(1, source, ["FR"], actor="System", goal="generate a unique code")
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({"stories": [{
        "source_requirement_id": 1,
        "title": "Generate code",
        "description": "As the system, I want to generate a unique code, so that each record is identifiable.",
        "acceptance_criteria": [
            "Given a record, when its code is generated, then the code is unique."
        ],
        "labels": ["FR"],
    }]})))

    with patch("app.nodes.generate.get_llm", return_value=mock_llm):
        result = await generate_node(state)

    assert result["user_stories"][0].description.startswith("As a system operator,")
