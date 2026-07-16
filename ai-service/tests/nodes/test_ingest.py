import pytest
from typing import Optional

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

    assert result["status"] == "ready_for_chunking"
    assert result["is_useful"] is True
    assert result["error"] is None
    assert result["relevance_score"] == pytest.approx(0.92)
    assert "[EMAIL]" in result["raw_text"]
    assert "[PHONE]" in result["raw_text"]
    assert "pm@example.com" not in result["raw_text"]
    assert "555-0199" not in result["raw_text"]


@pytest.mark.asyncio
async def test_ingest_multiple_raw_inputs_preserves_document_provenance(base_state, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(is_useful=True, relevance_score=0.9, reason="requirements")

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    state = base_state.copy()
    state.update({
        "file_type": "document",
        "raw_bytes": b"",
        "raw_inputs": [
            {
                "document_id": "doc-a",
                "filename": "a.txt",
                "file_type": "text",
                "mime_type": "text/plain",
                "sha256_hash": "a" * 64,
                "raw_bytes": b"The system must allow users to create an account and verify an email address.",
            },
            {
                "document_id": "doc-b",
                "filename": "b.txt",
                "file_type": "text",
                "mime_type": "text/plain",
                "sha256_hash": "b" * 64,
                "raw_bytes": b"Administrators must review audit events and export filtered security reports.",
            },
        ],
        "source_documents": [{"document_id": "doc-a"}, {"document_id": "doc-b"}],
    })

    result = await ingest.ingest_node(state)

    assert result["status"] == "ready_for_chunking"
    assert [(doc["document_id"], doc["filename"]) for doc in result["source_documents"]] == [
        ("doc-a", "a.txt"),
        ("doc-b", "b.txt"),
    ]
    assert all(doc["text"] for doc in result["source_documents"])


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

    def fake_extract(_: bytes) -> tuple[str, Optional[str]]:
        return "The system shall allow users to login securely.", None

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(ingest, "_extract_pdf", fake_extract)
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

    def fake_extract(_: bytes) -> tuple[str, Optional[str]]:
        return "I want to reset my password using my email.", None

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(ingest, "_extract_docx", fake_extract)
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


@pytest.mark.asyncio
async def test_ingest_only_uses_snippet_for_relevance(monkeypatch):
    """Verify that only a snippet is sent to relevance check."""
    captured_text = []

    async def fake_relevance(text: str) -> ingest.RelevanceCheck:
        captured_text.append(text)
        return ingest.RelevanceCheck(is_useful=True, relevance_score=1.0, reason="ok")

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    
    large_text = "Software " * 1000 # very big
    state = {
        "file_type": "text",
        "raw_bytes": large_text.encode("utf-8"),
    }
    
    await ingest.ingest_node(state)
    
    assert len(captured_text[0]) <= ingest.RELEVANCE_SNIPPET_CHARS
    assert captured_text[0] == large_text[:ingest.RELEVANCE_SNIPPET_CHARS]


def test_route_after_ingest_logic():
    """Test routing logic coverage."""
    # 1. transcribe
    assert ingest.route_after_ingest({"status": "to_transcribe"}) == "transcribe"
    # 2. extract
    assert ingest.route_after_ingest({"status": "ready_for_chunking"}) == "parse_to_chunks"
    # 3. audio -> transcribe
    assert ingest.route_after_ingest({"file_type": "audio"}) == "transcribe"
    # 4. error -> format
    assert ingest.route_after_ingest({"error": "err"}) == "format"
    # 5. rejected -> format
    assert ingest.route_after_ingest({"status": "rejected"}) == "format"
    # Default
    assert ingest.route_after_ingest({}) == "parse_to_chunks"


@pytest.mark.asyncio
async def test_mask_pii_detailed_categories(base_state, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(
            is_useful=True,
            relevance_score=0.95,
            reason="Software requirements content",
        )

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)

    text = (
        "Project config: we use openai token sk-proj-1234567890abcdef1234567890abcdef12345 "
        "and AWS credentials AKIA1234567890ABCDEF. "
        "GitHub token is ghp_1234567890abcdef1234567890abcdef1234. "
        "Google API key AIzaSy1234567890abcdef1234567890abcdef1. "
        "Huggingface token hf_1234567890abcdef1234567890abcdef12. "
        "Generic credentials: db_password = 'my_super_secret_password_123'. "
        "Valid Credit Card: 4012888888881881. "
        "Invalid Credit Card: 4012888888881882. "
        "False Positive Check: This project has 200 users and costs $5000. Transaction ID 123456."
    )

    state = base_state.copy()
    state["file_type"] = "text"
    state["raw_bytes"] = text.encode("utf-8")

    # Verify masking is active by default
    result = await ingest.ingest_node(state)
    assert result["status"] == "ready_for_chunking"
    assert result["pii_stats"] is not None
    assert result["pii_stats"]["api_keys"] == 6  # OpenAI, AWS, GitHub, Google, HF, db_password
    assert result["pii_stats"]["credit_cards"] == 1  # only the Luhn valid one

    masked_text = result["raw_text"]
    assert "[API_KEY]" in masked_text
    assert "[CREDIT_CARD]" in masked_text
    assert "sk-proj-" not in masked_text
    assert "AKIA" not in masked_text
    assert "ghp_" not in masked_text
    assert "AIzaSy" not in masked_text
    assert "hf_" not in masked_text
    assert "my_super_secret_password_123" not in masked_text
    
    # Valid CC masked
    assert "4012888888881881" not in masked_text
    # Invalid CC NOT masked
    assert "4012888888881882" in masked_text
    # Normal numbers NOT masked
    assert "200" in masked_text
    assert "5000" in masked_text
    assert "123456" in masked_text


@pytest.mark.asyncio
async def test_pii_masking_config_disabled(base_state, monkeypatch):
    async def fake_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(
            is_useful=True,
            relevance_score=0.95,
            reason="Software requirements content",
        )

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(ingest.settings, "ENABLE_PII_MASKING", False)

    text = "We use credit card 4012888888881881 and email contact@example.com."
    state = base_state.copy()
    state["file_type"] = "text"
    state["raw_bytes"] = text.encode("utf-8")

    result = await ingest.ingest_node(state)
    assert result["status"] == "ready_for_chunking"
    assert result["pii_stats"] is None
    assert "4012888888881881" in result["raw_text"]
    assert "contact@example.com" in result["raw_text"]

