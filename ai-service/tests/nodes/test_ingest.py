import pytest

import app.nodes.ingest as ingest


@pytest.mark.asyncio
async def test_ingest_audio_routes_to_transcribe(base_state):
    state = base_state.copy()
    state["file_type"] = "audio"
    state["raw_text"] = None

    result = await ingest.ingest_node(state)

    assert result["status"] == "to_transcribe"
    assert result["is_useful"] is True
    assert result["relevance_score"] == 1.0
    assert result["error"] is None


@pytest.mark.asyncio
async def test_ingest_text_masks_pii_and_marks_ready(base_state, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(
            is_useful=True,
            relevance_score=0.92,
            reason="Software requirements content",
        )

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)

    text = (
        "Project requirement: the backend API shall support user login and password reset. "
        "Contact pm@example.com or +1 (202) 555-0199 for sprint planning details."
    )

    state = base_state.copy()
    state["file_type"] = "text"
    state["raw_bytes"] = text.encode("utf-8")
    state["raw_text"] = None

    result = await ingest.ingest_node(state)

    assert result["status"] == "ready_for_extract"
    assert result["is_useful"] is True
    assert result["error"] is None
    assert result["relevance_score"] == pytest.approx(0.92)
    assert "[EMAIL]" in result["raw_text"]
    assert "[PHONE]" in result["raw_text"]
    assert "pm@example.com" not in result["raw_text"]
    assert "555-0199" not in result["raw_text"]


@pytest.mark.asyncio
async def test_ingest_rejects_short_text(base_state):
    state = base_state.copy()
    state["file_type"] = "text"
    state["raw_bytes"] = b"too short"
    state["raw_text"] = None

    result = await ingest.ingest_node(state)

    assert result["status"] == "rejected"
    assert result["is_useful"] is False
    assert result["relevance_score"] == 0.0
    assert result["error"].startswith("INGEST_EMPTY:")


@pytest.mark.asyncio
async def test_ingest_rejects_unsupported_file_type(base_state):
    state = base_state.copy()
    state["file_type"] = "xlsx"
    state["raw_bytes"] = b"fake bytes"
    state["raw_text"] = None

    result = await ingest.ingest_node(state)

    assert result["status"] == "rejected"
    assert result["is_useful"] is False
    assert result["error"].startswith("INGEST_FAILED:")
    assert "unsupported file_type" in result["error"]


@pytest.mark.asyncio
async def test_ingest_document_rejected_by_relevance(base_state, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(
            is_useful=False,
            relevance_score=0.08,
            reason="Random unrelated content",
        )

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)

    text = (
        "This document includes arbitrary shopping notes and unrelated items, "
        "plus random text to exceed fifty characters and trigger relevance filtering."
    )
    state = base_state.copy()
    state["file_type"] = "text"
    state["raw_bytes"] = text.encode("utf-8")
    state["raw_text"] = None

    result = await ingest.ingest_node(state)

    assert result["status"] == "rejected"
    assert result["is_useful"] is False
    assert result["relevance_score"] == pytest.approx(0.08)
    assert result["error"].startswith("DOCUMENT_REJECTED:")


@pytest.mark.asyncio
async def test_ingest_idempotent_for_same_input(base_state, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(
            is_useful=True,
            relevance_score=0.75,
            reason="Stable deterministic decision",
        )

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)

    text = (
        "Functional requirement: users shall create accounts and manage profile settings. "
        "Technical note: backend service exposes REST endpoints for account operations."
    )
    state = base_state.copy()
    state["file_type"] = "text"
    state["raw_bytes"] = text.encode("utf-8")
    state["raw_text"] = None

    first = await ingest.ingest_node(state)
    second = await ingest.ingest_node(state)

    assert first == second


@pytest.mark.asyncio
async def test_ingest_pdf_real(base_state, sample_pdf_bytes, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(
            is_useful=True,
            relevance_score=0.91,
            reason="Software requirements detected",
        )

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(ingest, "MIN_TEXT_LENGTH", 1)

    state = base_state.copy()
    state["raw_bytes"] = sample_pdf_bytes
    state["file_type"] = "pdf"

    result = await ingest.ingest_node(state)

    assert "raw_text" in result
    assert "allow users to login" in result["raw_text"].lower()
    assert result["is_useful"] is True
    assert result["relevance_score"] > 0.5


@pytest.mark.asyncio
async def test_ingest_docx_real(base_state, sample_docx_bytes, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(
            is_useful=True,
            relevance_score=0.89,
            reason="Software requirements detected",
        )

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(ingest, "MIN_TEXT_LENGTH", 1)

    state = base_state.copy()
    state["raw_bytes"] = sample_docx_bytes
    state["file_type"] = "docx"

    result = await ingest.ingest_node(state)

    assert "raw_text" in result
    assert "reset my password" in result["raw_text"].lower()
    assert result["is_useful"] is True


@pytest.mark.asyncio
async def test_ingest_invalid_file(base_state):
    state = base_state.copy()
    state["raw_bytes"] = b"not a pdf"
    state["file_type"] = "pdf"

    result = await ingest.ingest_node(state)

    # It should either error or reject
    assert "error" in result or result.get("is_useful") is False
