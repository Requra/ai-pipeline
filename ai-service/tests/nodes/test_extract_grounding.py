"""Phase 3 — grounded extraction: repair, confidence penalties, no raw logging."""

from __future__ import annotations

import json
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.nodes.extract import extract_node
from app.schemas.items import SourceChunk


def _state(base_state, **over):
    s = base_state.copy()
    s.update(over)
    return s


@pytest.mark.asyncio
async def test_extract_repairs_malformed_json(base_state):
    chunk = SourceChunk(
        chunk_id="c1",
        text="The system shall send confirmation emails to users.",
        start_char=0,
        end_char=51,
    )
    state = _state(base_state, chunks=[chunk])

    bad = "Here is the JSON: { oops not valid"
    good = json.dumps({
        "requirements": [{
            "id": 1,
            "text": "The system shall send confirmation emails.",
            "candidate_labels": ["FR"],
            "confidence": 0.9,
            "evidence": [{"chunk_id": "c1", "quote": "send confirmation emails"}],
        }]
    })
    mock_llm = MagicMock()
    # First call = extraction (bad), second call = repair (good).
    mock_llm.ainvoke = AsyncMock(side_effect=[MagicMock(content=bad), MagicMock(content=good)])

    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    assert result["status"] == "success"
    assert len(result["extracted_requirements"]) == 1
    assert mock_llm.ainvoke.call_count == 2  # extraction + one repair


@pytest.mark.asyncio
async def test_extract_lowers_confidence_for_fallback_evidence(base_state):
    # Quote that does NOT appear in the chunk -> snippet fallback -> penalty.
    chunk = SourceChunk(
        chunk_id="c1",
        text="The system shall archive invoices monthly.",
        start_char=0,
        end_char=42,
    )
    state = _state(base_state, chunks=[chunk])
    content = json.dumps({
        "requirements": [{
            "id": 1,
            "text": "The system shall archive invoices.",
            "candidate_labels": ["FR"],
            "confidence": 1.0,
            "evidence": [{"chunk_id": "c1", "quote": "totally unrelated quote not in source"}],
        }]
    })
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=content))

    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    req = result["extracted_requirements"][0]
    assert req.needs_review is True
    assert req.confidence < 1.0  # penalised for weak evidence
    # Aggregate weak-evidence warning surfaced.
    assert any(w["code"] == "EXTRACT_WEAK_EVIDENCE" for w in result.get("warnings", []))


@pytest.mark.asyncio
async def test_extract_keeps_confidence_for_exact_evidence(base_state):
    chunk = SourceChunk(
        chunk_id="c1",
        text="The system shall export reports to PDF.",
        start_char=0,
        end_char=39,
    )
    state = _state(base_state, chunks=[chunk])
    content = json.dumps({
        "requirements": [{
            "id": 1,
            "text": "The system shall export reports to PDF.",
            "candidate_labels": ["FR"],
            "confidence": 0.95,
            "evidence": [{"chunk_id": "c1", "quote": "export reports to PDF"}],
        }]
    })
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=content))

    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        result = await extract_node(state)

    req = result["extracted_requirements"][0]
    # Exact quote present in chunk -> confidence preserved.
    assert req.confidence == 0.95
    assert "EXTRACT_WEAK_EVIDENCE" not in [w["code"] for w in result.get("warnings", [])]


@pytest.mark.asyncio
async def test_extract_no_raw_logging_in_production(base_state, monkeypatch, caplog):
    import app.nodes.extract as ex

    # Even with the debug flag ON, production must never log raw model output.
    monkeypatch.setattr(ex.settings, "ENV", "production")
    monkeypatch.setattr(ex.settings, "DEBUG_LLM_IO", True)

    sentinel = "SENTINEL_RAW_LLM_OUTPUT_9Z"
    state = _state(base_state, raw_text="CONFIDENTIAL merger details for project bluebird.")
    content = json.dumps({
        "requirements": [{
            "id": 1,
            "text": "The system shall record deals.",
            "candidate_labels": ["FR"],
            "confidence": 0.9,
            "evidence": [{"chunk_id": "c", "quote": "record deals"}],
            "note": sentinel,
        }]
    })
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=content))

    with patch("app.nodes.extract.get_llm", return_value=mock_llm):
        with caplog.at_level(logging.DEBUG):
            await extract_node(state)

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert sentinel not in joined
    assert "CONFIDENTIAL merger" not in joined
