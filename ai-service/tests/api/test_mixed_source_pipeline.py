from __future__ import annotations

import io
import json
import zipfile
import pytest
from unittest.mock import AsyncMock, patch

from app.graph.pipeline import build_pipeline
from app.worker.state import build_worker_initial_state, stash_input
from app.store.factory import get_stores, reset_stores
from app.store.models import AiJobRecord, InputType, SourceDocumentRecord
from app.schemas.items import SourceChunk
from app.schemas.pipeline_state import PipelineState
from app.services.source_processing import audio, document
from app.nodes import ingest
from app.nodes.ingest import RelevanceCheck


@pytest.fixture(autouse=True)
def _isolate():
    reset_stores()
    yield
    reset_stores()


@pytest.fixture
def mock_relevance(monkeypatch):
    async def fake_relevance(text: str) -> RelevanceCheck:
        return RelevanceCheck(is_useful=True, relevance_score=0.96, reason="software delivery requirements")

    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(document, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(audio, "_run_relevance_check", fake_relevance)


@pytest.fixture(autouse=True)
def mock_llm_pipeline(monkeypatch):
    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "get_audio_duration_seconds", lambda *args, **kwargs: 10.0)

    from unittest.mock import MagicMock
    from app import llm
    from app.nodes import extract, classify, generate, summarize, ingest
    from app.services.source_processing import extractors

    async def fake_llm_ainvoke(messages, **kwargs):
        system = messages[0][1] if isinstance(messages, list) and len(messages) > 0 else ""
        if "Extract atomic software requirements" in system or "Extract requirements" in system:
            return MagicMock(content=json.dumps({
                "requirements": [
                    {
                        "id": 1,
                        "text": "The backend API shall rate limit callers to 50 requests per second.",
                        "actor": "System",
                        "goal": "rate limiting",
                        "candidate_labels": ["NFR"],
                        "confidence": 0.95,
                        "evidence": [{"chunk_id": "c1", "quote": "rate limit callers to 50 requests per second"}]
                    },
                    {
                        "id": 2,
                        "text": "The mobile app must support biometric authentication using FaceID and Fingerprint.",
                        "actor": "User",
                        "goal": "biometric authentication",
                        "candidate_labels": ["FR"],
                        "confidence": 0.95,
                        "evidence": [{"chunk_id": "c2", "quote": "support biometric authentication using FaceID and Fingerprint"}]
                    }
                ]
            }))
        if "You classify each requirement" in system:
            return MagicMock(content=json.dumps({
                "classifications": [
                    {"id": 1, "labels": ["NFR"], "confidence": 0.95},
                    {"id": 2, "labels": ["FR"], "confidence": 0.95}
                ]
            }))
        if "Convert requirements into USER STORIES" in system or "user stories" in system.lower():
            return MagicMock(content=json.dumps({
                "stories": [
                    {
                        "source_requirement_ids": [2],
                        "title": "Biometric Authentication",
                        "description": "As a user, I want to log in using FaceID or Fingerprint, so that authentication is fast and secure.",
                        "acceptance_criteria": [
                            "Given the login screen, when the user selects FaceID, then the app authenticates the biometric profile.",
                            "Given invalid biometric input, when authentication fails, then the app prompts for PIN entry."
                        ],
                        "labels": ["FR"],
                        "story_points": 3
                    }
                ]
            }))
        return MagicMock(content=json.dumps({"executive_summary": "Summary of mixed sources", "scope": ["biometrics", "rate limit"]}))

    mock_llm_client = MagicMock()
    mock_llm_client.ainvoke = fake_llm_ainvoke
    monkeypatch.setattr(llm, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extract, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(classify, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(generate, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(summarize, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(ingest, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extractors, "get_llm", lambda: mock_llm_client)


@pytest.mark.asyncio
async def test_full_pipeline_mixed_sources_e2e(mock_relevance, monkeypatch):
    """
    Test full LangGraph pipeline execution with mixed PDF/TXT document and Audio inputs.
    Verifies that chunks converge before source indexing and requirements extraction.
    """
    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)

    async def fake_transcribe(*args, **kwargs):
        utterances = [
            {
                "speaker": "ProductManager",
                "start": 0.0,
                "end": 4.5,
                "text": "The mobile app must support biometric authentication using FaceID and Fingerprint.",
                "confidence": 0.96,
            }
        ]
        return "The mobile app must support biometric authentication using FaceID and Fingerprint.", utterances

    monkeypatch.setattr(audio, "_transcribe_groq", fake_transcribe)

    compiled_graph = build_pipeline()

    initial_state = {
        "job_id": "mixed-e2e-1",
        "tenant_id": "ten-1",
        "project_id": "proj-1",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {
                "document_id": "doc_arch_spec",
                "filename": "architecture_spec.txt",
                "file_type": "text",
                "mime_type": "text/plain",
                "raw_bytes": b"Requirement REQ-01: The backend API shall rate limit callers to 50 requests per second.",
            },
            {
                "document_id": "audio_interview_1",
                "filename": "stakeholder_interview.mp3",
                "file_type": "audio",
                "mime_type": "audio/mpeg",
                "audio_format": "mp3",
                "raw_bytes": b"ID3\x03fake-audio-bytes",
            },
        ],
        "source_documents": [
            {
                "document_id": "doc_arch_spec",
                "filename": "architecture_spec.txt",
                "file_type": "text",
                "mime_type": "text/plain",
            },
            {
                "document_id": "audio_interview_1",
                "filename": "stakeholder_interview.mp3",
                "file_type": "audio",
                "mime_type": "audio/mpeg",
            },
        ],
        "chunks": [],
        "source_index_id": None,
        "retrieval_stats": None,
        "pii_stats": None,
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "quality_report": None,
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.0,
        "status": "started",
        "error": None,
        "started_at": 0.0,
        "processing_time_ms": 0,
        "repair_attempts": 0,
        "resolved_quality_issues": [],
        "functional_requirements": [],
        "processed_sources": None,
        "source_processing_stats": None,
        "partial_source_failure": False,
    }

    final_state = await compiled_graph.ainvoke(initial_state)

    assert final_state["is_useful"] is True
    assert final_state["status"] in ("completed", "partial")

    job_result = final_state.get("job_result")
    assert job_result is not None
    assert len(job_result.source_documents) == 2

    source_types = {doc.source_type for doc in job_result.source_documents}
    assert source_types == {"text", "audio"}
    source_ids = {doc.source_id for doc in job_result.source_documents}
    assert source_ids == {"doc_arch_spec", "audio_interview_1"}

    # Verify that unified chunks contain both document and audio provenance
    chunks = final_state["chunks"]
    assert len(chunks) >= 2
    assert any(c.document_id == "doc_arch_spec" for c in chunks)
    assert any(c.document_id == "audio_interview_1" and c.speaker == "ProductManager" for c in chunks)


@pytest.mark.asyncio
async def test_worker_state_recovery_backend_sources():
    """
    Test worker reconstructing state from PostgreSQL / backend document client
    for InputType.BACKEND_SOURCES.
    """
    stores = get_stores()

    job = AiJobRecord(
        job_id="worker-rec-sources-1",
        tenant_id="ten-1",
        project_id="proj-1",
        input_type=InputType.BACKEND_SOURCES.value,
        requested_by="tester",
    )
    await stores.jobs.create_job(job)

    doc1 = SourceDocumentRecord(
        job_id="worker-rec-sources-1",
        backend_document_id="doc_spec",
        file_name="spec.txt",
        source_type="text",
        mime_type="text/plain",
        file_size_bytes=100,
        sha256_hash="hash1",
    )
    doc2 = SourceDocumentRecord(
        job_id="worker-rec-sources-1",
        backend_document_id="audio_meeting",
        file_name="meeting.mp3",
        source_type="audio",
        mime_type="audio/mpeg",
        file_size_bytes=200,
        sha256_hash="hash2",
    )
    await stores.chunks.save_documents([doc1, doc2])

    class FakeBackendClient:
        async def fetch_document_bytes(self, ref):
            if ref.get("document_id") == "doc_spec":
                return b"The system must authenticate users via JWT."
            elif ref.get("document_id") == "audio_meeting":
                return b"ID3\x03fake-audio-bytes"
            return b""

    fake_client = FakeBackendClient()
    state = await build_worker_initial_state(job, stores, backend_client=fake_client)

    assert state["job_id"] == "worker-rec-sources-1"
    assert state["file_type"] == "sources"
    assert len(state["raw_inputs"]) == 2

    input_by_id = {item["document_id"]: item for item in state["raw_inputs"]}
    assert input_by_id["doc_spec"]["file_type"] == "text"
    assert input_by_id["audio_meeting"]["file_type"] == "audio"
    assert input_by_id["audio_meeting"]["audio_format"] == "mp3"


@pytest.mark.asyncio
async def test_full_pipeline_mixed_sources_partial_failure(mock_relevance, monkeypatch):
    """
    Test full pipeline when audio fails but document succeeds.
    Pipeline continues with usable document chunks and reports partial status.
    """
    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)

    async def fail_transcribe(*args, **kwargs):
        raise RuntimeError("STT provider timeout")

    monkeypatch.setattr(audio, "_transcribe_groq", fail_transcribe)
    monkeypatch.setattr(audio, "_transcribe_deepgram", fail_transcribe)

    compiled_graph = build_pipeline()

    initial_state = {
        "job_id": "mixed-partial-e2e",
        "tenant_id": "ten-1",
        "project_id": "proj-1",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {
                "document_id": "doc_valid",
                "filename": "requirements.txt",
                "file_type": "text",
                "mime_type": "text/plain",
                "raw_bytes": b"Requirement: The system shall provide a search bar for filtering products by tag.",
            },
            {
                "document_id": "audio_failed",
                "filename": "bad_audio.mp3",
                "file_type": "audio",
                "mime_type": "audio/mpeg",
                "audio_format": "mp3",
                "raw_bytes": b"ID3\x03fake-audio",
            },
        ],
        "source_documents": [
            {"document_id": "doc_valid", "filename": "requirements.txt", "file_type": "text"},
            {"document_id": "audio_failed", "filename": "bad_audio.mp3", "file_type": "audio"},
        ],
        "chunks": [],
        "source_index_id": None,
        "retrieval_stats": None,
        "pii_stats": None,
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "quality_report": None,
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.0,
        "status": "started",
        "error": None,
        "started_at": 0.0,
        "processing_time_ms": 0,
        "repair_attempts": 0,
        "resolved_quality_issues": [],
        "functional_requirements": [],
        "processed_sources": None,
        "source_processing_stats": None,
        "partial_source_failure": False,
    }

    final_state = await compiled_graph.ainvoke(initial_state)

    assert final_state["is_useful"] is True
    assert final_state["partial_source_failure"] is True
    assert final_state["status"] == "partial"
    assert len(final_state["chunks"]) >= 1
    assert all(c.document_id == "doc_valid" for c in final_state["chunks"])

    job_result = final_state.get("job_result")
    assert job_result is not None
    assert job_result.status == "partial"
    warning_codes = [w.code if hasattr(w, "code") else w.get("code") for w in job_result.warnings]
    assert "PARTIAL_SOURCE_FAILURE" in warning_codes


@pytest.mark.asyncio
async def test_detect_file_type_mixed_sources():
    """Verify detect_file_type_node correctly classifies and tags mixed inputs."""
    from app.nodes.detect_file_type import detect_file_type_node

    pdf_bytes = b"%PDF-1.4 header and minimal pdf content"
    docx_bio = io.BytesIO()
    with zipfile.ZipFile(docx_bio, "w") as z:
        z.writestr("[Content_Types].xml", "<types></types>")
        z.writestr("word/document.xml", "<document></document>")
    docx_bytes = docx_bio.getvalue()
    mp3_bytes = b"ID3\x03fake-audio"

    state = {
        "job_id": "detect-mixed-1",
        "raw_inputs": [
            {"document_id": "doc_1", "filename": "spec.pdf", "raw_bytes": pdf_bytes},
            {"document_id": "doc_2", "filename": "notes.docx", "raw_bytes": docx_bytes},
            {"document_id": "doc_3", "filename": "meeting.mp3", "raw_bytes": mp3_bytes},
        ],
        "source_documents": [],
    }

    res = await detect_file_type_node(state)
    assert res["status"] == "type_detected"
    assert res["file_type"] == "sources"
    assert res["audio_format"] == "mp3"
    assert len(res["raw_inputs"]) == 3
    assert [item["file_type"] for item in res["raw_inputs"]] == ["pdf", "docx", "audio"]


@pytest.mark.asyncio
async def test_detect_file_type_rejects_multiple_audio(monkeypatch):
    """Verify detect_file_type_node enforces MAX_AUDIO_SOURCES_PER_JOB limit."""
    from app.nodes.detect_file_type import detect_file_type_node
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_AUDIO_SOURCES_PER_JOB", 2)

    mp3_1 = b"ID3\x03audio1"
    mp3_2 = b"ID3\x03audio2"
    mp3_3 = b"ID3\x03audio3"

    state = {
        "job_id": "detect-multi-audio",
        "raw_inputs": [
            {"document_id": "doc_1", "filename": "spec.txt", "raw_bytes": b"Some text content for software specs"},
            {"document_id": "audio_1", "filename": "meeting1.mp3", "raw_bytes": mp3_1},
            {"document_id": "audio_2", "filename": "meeting2.mp3", "raw_bytes": mp3_2},
            {"document_id": "audio_3", "filename": "meeting3.mp3", "raw_bytes": mp3_3},
        ],
        "source_documents": [],
    }

    res = await detect_file_type_node(state)
    assert res["status"] == "rejected"
    assert "audio source count (3) exceeds maximum allowed" in res["error"]


def test_mixed_sources_fingerprint_idempotency():
    """Verify deterministic SHA-256 fingerprinting for backend_sources."""
    from app.services.fingerprint import compute_job_request_fingerprint
    from app.api.schemas import CreateJobRequest, SourceDocumentIn, JobOptionsIn

    req1 = CreateJobRequest(
        job_id="fp-mixed-1",
        tenant_id="ten-1",
        project_id="proj-1",
        input_type="backend_sources",
        source_documents=[
            SourceDocumentIn(document_id="d1", filename="a.pdf", file_type="pdf", sha256_hash="hash_a"),
            SourceDocumentIn(document_id="d2", filename="b.mp3", file_type="audio", sha256_hash="hash_b"),
        ],
        options=JobOptionsIn(language="en"),
    )

    req2 = CreateJobRequest(
        job_id="fp-mixed-2",  # Different job_id, same content
        tenant_id="ten-1",
        project_id="proj-1",
        input_type="backend_sources",
        source_documents=[
            SourceDocumentIn(document_id="d2", filename="b.mp3", file_type="audio", sha256_hash="hash_b"),
            SourceDocumentIn(document_id="d1", filename="a.pdf", file_type="pdf", sha256_hash="hash_a"),
        ],  # Swapped order
        options=JobOptionsIn(language="en"),
    )

    fp1 = compute_job_request_fingerprint(req1)
    fp2 = compute_job_request_fingerprint(req2)
    assert fp1 == fp2  # Order-insensitive and deterministic

    # Changed hash changes fingerprint
    req3 = CreateJobRequest(
        job_id="fp-mixed-3",
        tenant_id="ten-1",
        project_id="proj-1",
        input_type="backend_sources",
        source_documents=[
            SourceDocumentIn(document_id="d1", filename="a.pdf", file_type="pdf", sha256_hash="hash_a_MODIFIED"),
            SourceDocumentIn(document_id="d2", filename="b.mp3", file_type="audio", sha256_hash="hash_b"),
        ],
        options=JobOptionsIn(language="en"),
    )
    fp3 = compute_job_request_fingerprint(req3)
    assert fp3 != fp1

