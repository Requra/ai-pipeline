from __future__ import annotations

import io
import json
import fitz
import docx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.graph.pipeline import build_pipeline
from app.worker.state import build_worker_initial_state, stash_input, load_input
from app.store.factory import get_stores, reset_stores
from app.store.models import AiJobRecord, InputType, SourceDocumentRecord, SourceChunkRecord
from app.schemas.items import SourceChunk, DocumentSource
from app.schemas.pipeline_state import PipelineState
from app.services.source_processing import audio, document
from app.services.source_processing.extractors import RelevanceCheckResult
from app.services.fingerprint import compute_job_request_fingerprint
from app.api.schemas import CreateJobRequest, SourceDocumentIn, JobOptionsIn
from app.services.audio_semantics import reconstruct_audio_chunks
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(autouse=True)
def _isolate():
    reset_stores()
    yield
    reset_stores()


def _make_sample_docx(text: str = "The system shall enforce TLS 1.3 encryption across all communication channels.") -> bytes:
    doc = docx.Document()
    doc.add_paragraph(text)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def _make_sample_pdf(text: str = "The application shall support multi-factor authentication for administrators.") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text)
    return doc.tobytes()


@pytest.fixture
def mock_pipeline_environment(monkeypatch):
    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "get_audio_duration_seconds", lambda b, *a, **k: 15.0 if b else 0.0)

    async def fake_relevance(text: str, *args, **kwargs) -> RelevanceCheckResult:
        return RelevanceCheckResult(is_useful=True, relevance_score=0.95, reason="Software requirement analysis")

    from app.services.source_processing import extractors
    monkeypatch.setattr(extractors, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(document, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(audio, "_run_relevance_check", fake_relevance)

    async def fake_llm_ainvoke(messages, **kwargs):
        system = messages[0][1] if isinstance(messages, list) and len(messages) > 0 else ""
        if "Extract atomic software requirements" in system or "Extract requirements" in system:
            return MagicMock(content=json.dumps({
                "requirements": [
                    {
                        "id": 1,
                        "text": "The password reset token expires after 15 minutes.",
                        "actor": "System",
                        "goal": "password reset expiration",
                        "candidate_labels": ["FR"],
                        "confidence": 0.95,
                        "evidence": [{"chunk_id": "c1", "quote": "password reset token expires after 15 minutes"}]
                    },
                    {
                        "id": 2,
                        "text": "Audit events must be retained for 120 days.",
                        "actor": "System",
                        "goal": "audit retention",
                        "candidate_labels": ["NFR"],
                        "confidence": 0.92,
                        "evidence": [{"chunk_id": "c2", "quote": "Audit events must be retained for 120 days"}]
                    }
                ]
            }))
        if "You classify each requirement" in system:
            return MagicMock(content=json.dumps({
                "classifications": [
                    {"id": 1, "labels": ["FR"], "confidence": 0.95},
                    {"id": 2, "labels": ["NFR"], "confidence": 0.92}
                ]
            }))
        if "Convert requirements into USER STORIES" in system or "user stories" in system.lower():
            return MagicMock(content=json.dumps({
                "stories": [
                    {
                        "source_requirement_ids": [1],
                        "title": "Password Reset Expiry",
                        "description": "As a user, I want password reset tokens to expire in 15 minutes to keep my account secure.",
                        "acceptance_criteria": [
                            "Given an expired token, when user clicks reset, then token is rejected."
                        ],
                        "labels": ["FR"],
                        "story_points": 2
                    }
                ]
            }))
        return MagicMock(content=json.dumps({"executive_summary": "Comprehensive multi-source analysis.", "scope": ["security", "audit"]}))

    from app import llm
    from app.nodes import extract, classify, generate, summarize
    mock_client = MagicMock()
    mock_client.ainvoke = fake_llm_ainvoke
    monkeypatch.setattr(llm, "get_llm", lambda: mock_client)
    monkeypatch.setattr(extract, "get_llm", lambda: mock_client)
    monkeypatch.setattr(classify, "get_llm", lambda: mock_client)
    monkeypatch.setattr(generate, "get_llm", lambda: mock_client)
    monkeypatch.setattr(summarize, "get_llm", lambda: mock_client)


# ===========================================================================
# TEST MATRIX CASES
# ===========================================================================

# Case A: Multi-audio only (3 MP3 recordings)
@pytest.mark.asyncio
async def test_case_a_multi_audio_only(mock_pipeline_environment, monkeypatch):
    """Case A: Multi-audio only -> accepted, processed, unique chunk IDs, correct provenance."""
    async def fake_transcribe(raw_bytes, file_subtype, job_id, language, document_id=None):
        utterances = [
            {
                "speaker": f"Speaker_{document_id}",
                "start": 0.0,
                "end": 10.0,
                "text": f"Requirement from audio {document_id}: The password reset token expires after 15 minutes.",
                "confidence": 0.95,
            }
        ]
        return f"Audio transcript for {document_id}", utterances

    monkeypatch.setattr(audio, "_transcribe_groq", fake_transcribe)

    compiled_graph = build_pipeline()
    initial_state = {
        "job_id": "job_multi_audio_3",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {"document_id": "aud_1", "filename": "meeting1.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03audio1"},
            {"document_id": "aud_2", "filename": "meeting2.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03audio2"},
            {"document_id": "aud_3", "filename": "meeting3.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03audio3"},
        ],
        "source_documents": [
            {"document_id": "aud_1", "filename": "meeting1.mp3", "file_type": "audio"},
            {"document_id": "aud_2", "filename": "meeting2.mp3", "file_type": "audio"},
            {"document_id": "aud_3", "filename": "meeting3.mp3", "file_type": "audio"},
        ],
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    assert final_state["is_useful"] is True
    assert final_state["status"] in ("completed", "partial")

    chunks = final_state["chunks"]
    assert len(chunks) == 3
    chunk_ids = [c.chunk_id for c in chunks]
    assert len(set(chunk_ids)) == 3, "All audio chunks must have globally unique chunk IDs"
    assert all("aud_" in cid for cid in chunk_ids), "Audio chunk IDs must incorporate source document ID"
    doc_ids = {c.document_id for c in chunks}
    assert doc_ids == {"aud_1", "aud_2", "aud_3"}


# Case B: Multi-document only (2 PDF, 2 DOCX, 2 TXT)
@pytest.mark.asyncio
async def test_case_b_multi_document_only(mock_pipeline_environment):
    """Case B: Multi-document only -> 6 documents processed and merged."""
    compiled_graph = build_pipeline()
    initial_state = {
        "job_id": "job_multi_doc_6",
        "file_type": "document",
        "language": "en",
        "raw_inputs": [
            {"document_id": "pdf_1", "filename": "req1.pdf", "file_type": "pdf", "mime_type": "application/pdf", "raw_bytes": _make_sample_pdf("PDF 1 req")},
            {"document_id": "pdf_2", "filename": "req2.pdf", "file_type": "pdf", "mime_type": "application/pdf", "raw_bytes": _make_sample_pdf("PDF 2 req")},
            {"document_id": "docx_1", "filename": "arch1.docx", "file_type": "docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "raw_bytes": _make_sample_docx("Docx 1 requirement")},
            {"document_id": "docx_2", "filename": "arch2.docx", "file_type": "docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "raw_bytes": _make_sample_docx("Docx 2 requirement")},
            {"document_id": "txt_1", "filename": "notes1.txt", "file_type": "text", "mime_type": "text/plain", "raw_bytes": b"Requirement: The system shall support multi-tenancy."},
            {"document_id": "txt_2", "filename": "notes2.txt", "file_type": "text", "mime_type": "text/plain", "raw_bytes": b"Requirement: The API shall enforce TLS 1.3 encryption."},
        ],
        "source_documents": [
            {"document_id": "pdf_1", "filename": "req1.pdf", "file_type": "pdf"},
            {"document_id": "pdf_2", "filename": "req2.pdf", "file_type": "pdf"},
            {"document_id": "docx_1", "filename": "arch1.docx", "file_type": "docx"},
            {"document_id": "docx_2", "filename": "arch2.docx", "file_type": "docx"},
            {"document_id": "txt_1", "filename": "notes1.txt", "file_type": "text"},
            {"document_id": "txt_2", "filename": "notes2.txt", "file_type": "text"},
        ],
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    assert final_state["is_useful"] is True
    assert final_state["status"] in ("completed", "partial")
    doc_ids = {c.document_id for c in final_state["chunks"]}
    assert doc_ids == {"pdf_1", "pdf_2", "docx_1", "docx_2", "txt_1", "txt_2"}


# Case C: Full heterogeneous job (2 MP3, 2 PDF, 2 DOCX, 2 TXT)
@pytest.mark.asyncio
async def test_case_c_full_heterogeneous_job(mock_pipeline_environment, monkeypatch):
    """Case C: Full 8-source heterogeneous job -> unified corpus."""
    async def fake_transcribe(raw_bytes, file_subtype, job_id, language, document_id=None):
        return f"Audio transcript for {document_id}", [
            {"speaker": f"Speaker_{document_id}", "start": 0.0, "end": 10.0, "text": f"Audio spoken content for {document_id}", "confidence": 0.95}
        ]
    monkeypatch.setattr(audio, "_transcribe_groq", fake_transcribe)

    compiled_graph = build_pipeline()
    raw_inputs = [
        {"document_id": "mp3_1", "filename": "m1.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03m1"},
        {"document_id": "mp3_2", "filename": "m2.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03m2"},
        {"document_id": "pdf_1", "filename": "d1.pdf", "file_type": "pdf", "mime_type": "application/pdf", "raw_bytes": _make_sample_pdf("PDF 1 req")},
        {"document_id": "pdf_2", "filename": "d2.pdf", "file_type": "pdf", "mime_type": "application/pdf", "raw_bytes": _make_sample_pdf("PDF 2 req")},
        {"document_id": "docx_1", "filename": "d1.docx", "file_type": "docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "raw_bytes": _make_sample_docx("DOCX 1 req")},
        {"document_id": "docx_2", "filename": "d2.docx", "file_type": "docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "raw_bytes": _make_sample_docx("DOCX 2 req")},
        {"document_id": "txt_1", "filename": "t1.txt", "file_type": "text", "mime_type": "text/plain", "raw_bytes": b"Requirement text 1"},
        {"document_id": "txt_2", "filename": "t2.txt", "file_type": "text", "mime_type": "text/plain", "raw_bytes": b"Requirement text 2"},
    ]
    source_docs = [{"document_id": i["document_id"], "filename": i["filename"], "file_type": i["file_type"]} for i in raw_inputs]

    initial_state = {
        "job_id": "job_hetero_8",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": raw_inputs,
        "source_documents": source_docs,
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    assert final_state["is_useful"] is True
    assert final_state["status"] in ("completed", "partial")
    represented_docs = {c.document_id for c in final_state["chunks"]}
    assert len(represented_docs) == 8


# Case D: One bad audio (valid audio + broken audio + valid PDF -> PARTIAL)
@pytest.mark.asyncio
async def test_case_d_one_bad_audio(mock_pipeline_environment, monkeypatch):
    """Case D: One bad audio fails while valid audio and PDF succeed -> PARTIAL."""
    async def selective_transcribe(*args, **kwargs):
        doc_id = kwargs.get("document_id")
        if not doc_id and len(args) >= 5:
            doc_id = args[4]
        if doc_id == "aud_broken":
            raise RuntimeError("Audio codec corruption")
        return "Good audio content", [{"speaker": "S1", "start": 0.0, "end": 5.0, "text": "Valid spoken requirement", "confidence": 0.95}]

    monkeypatch.setattr(audio, "_transcribe_groq", selective_transcribe)
    monkeypatch.setattr(audio, "_transcribe_deepgram", selective_transcribe)

    compiled_graph = build_pipeline()
    initial_state = {
        "job_id": "job_one_bad_audio",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {"document_id": "aud_valid", "filename": "good.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03good"},
            {"document_id": "aud_broken", "filename": "bad.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03bad"},
            {"document_id": "pdf_valid", "filename": "good.pdf", "file_type": "pdf", "mime_type": "application/pdf", "raw_bytes": _make_sample_pdf("Good PDF req")},
        ],
        "source_documents": [
            {"document_id": "aud_valid", "filename": "good.mp3", "file_type": "audio"},
            {"document_id": "aud_broken", "filename": "bad.mp3", "file_type": "audio"},
            {"document_id": "pdf_valid", "filename": "good.pdf", "file_type": "pdf"},
        ],
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    assert final_state["is_useful"] is True
    assert final_state["partial_source_failure"] is True
    assert final_state["status"] == "partial"
    represented_docs = {c.document_id for c in final_state["chunks"]}
    assert represented_docs == {"aud_valid", "pdf_valid"}


# Case E: Two bad audio + useful documents -> PARTIAL
@pytest.mark.asyncio
async def test_case_e_two_bad_audio_useful_documents(mock_pipeline_environment, monkeypatch):
    """Case E: Two bad audio fail, documents continue -> PARTIAL."""
    async def fail_all_transcribe(*args, **kwargs):
        raise RuntimeError("STT quota exceeded")

    monkeypatch.setattr(audio, "_transcribe_groq", fail_all_transcribe)
    monkeypatch.setattr(audio, "_transcribe_deepgram", fail_all_transcribe)

    compiled_graph = build_pipeline()
    initial_state = {
        "job_id": "job_two_bad_audio",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {"document_id": "aud_1", "filename": "m1.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03m1"},
            {"document_id": "aud_2", "filename": "m2.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03m2"},
            {"document_id": "txt_1", "filename": "req.txt", "file_type": "text", "mime_type": "text/plain", "raw_bytes": b"Requirement: The system shall encrypt all data at rest."},
        ],
        "source_documents": [
            {"document_id": "aud_1", "filename": "m1.mp3", "file_type": "audio"},
            {"document_id": "aud_2", "filename": "m2.mp3", "file_type": "audio"},
            {"document_id": "txt_1", "filename": "req.txt", "file_type": "text"},
        ],
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    assert final_state["is_useful"] is True
    assert final_state["partial_source_failure"] is True
    assert final_state["status"] == "partial"
    represented_docs = {c.document_id for c in final_state["chunks"]}
    assert represented_docs == {"txt_1"}


# Case F: Documents fail + one useful audio -> PARTIAL
@pytest.mark.asyncio
async def test_case_f_documents_fail_useful_audio(mock_pipeline_environment, monkeypatch):
    """Case F: Corrupted document fails, audio succeeds -> PARTIAL."""
    async def good_transcribe(raw_bytes, file_subtype, job_id, language, document_id=None):
        return "Audio spoken requirements", [{"speaker": "S1", "start": 0.0, "end": 5.0, "text": "Audio requirement text", "confidence": 0.95}]

    monkeypatch.setattr(audio, "_transcribe_groq", good_transcribe)

    compiled_graph = build_pipeline()
    initial_state = {
        "job_id": "job_doc_fail_audio_ok",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {"document_id": "aud_1", "filename": "m1.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03m1"},
            {"document_id": "docx_bad", "filename": "corrupted.docx", "file_type": "docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "raw_bytes": b"Not a real zip archive"},
        ],
        "source_documents": [
            {"document_id": "aud_1", "filename": "m1.mp3", "file_type": "audio"},
            {"document_id": "docx_bad", "filename": "corrupted.docx", "file_type": "docx"},
        ],
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    assert final_state["is_useful"] is True
    assert final_state["partial_source_failure"] is True
    assert final_state["status"] == "partial"
    represented_docs = {c.document_id for c in final_state["chunks"]}
    assert represented_docs == {"aud_1"}


# Case G: All sources fail -> FAILED
@pytest.mark.asyncio
async def test_case_g_all_sources_fail(mock_pipeline_environment, monkeypatch):
    """Case G: All sources fail -> status=failed in job_result."""
    async def fail_transcribe(*args, **kwargs):
        raise RuntimeError("STT crash")
    monkeypatch.setattr(audio, "_transcribe_groq", fail_transcribe)
    monkeypatch.setattr(audio, "_transcribe_deepgram", fail_transcribe)

    compiled_graph = build_pipeline()
    initial_state = {
        "job_id": "job_all_fail",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {"document_id": "aud_1", "filename": "m1.mp3", "file_type": "audio", "mime_type": "audio/mpeg", "audio_format": "mp3", "raw_bytes": b"ID3\x03m1"},
            {"document_id": "docx_bad", "filename": "corrupted.docx", "file_type": "docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "raw_bytes": b"Bad zip"},
        ],
        "source_documents": [
            {"document_id": "aud_1", "filename": "m1.mp3", "file_type": "audio"},
            {"document_id": "docx_bad", "filename": "corrupted.docx", "file_type": "docx"},
        ],
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    job_result = final_state.get("job_result")
    assert job_result is not None
    assert job_result.status == "failed"
    assert "ALL_SOURCES_FAILED" in (final_state.get("error") or "")


# Case H: All irrelevant -> REJECTED
@pytest.mark.asyncio
async def test_case_h_all_irrelevant(mock_pipeline_environment, monkeypatch):
    """Case H: All sources valid but irrelevant -> status=rejected in job_result."""
    async def fake_irrelevant(text: str, *args, **kwargs) -> RelevanceCheckResult:
        return RelevanceCheckResult(is_useful=False, relevance_score=0.10, reason="Personal grocery recipe list")

    from app.services.source_processing import extractors
    monkeypatch.setattr(extractors, "_run_relevance_check", fake_irrelevant)
    monkeypatch.setattr(document, "_run_relevance_check", fake_irrelevant)
    monkeypatch.setattr(audio, "_run_relevance_check", fake_irrelevant)

    compiled_graph = build_pipeline()
    initial_state = {
        "job_id": "job_all_irrelevant",
        "file_type": "sources",
        "language": "en",
        "raw_inputs": [
            {"document_id": "txt_1", "filename": "recipe.txt", "file_type": "text", "mime_type": "text/plain", "raw_bytes": b"Buy apples and milk"},
        ],
        "source_documents": [{"document_id": "txt_1", "filename": "recipe.txt", "file_type": "text"}],
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "warnings": [],
        "is_useful": True,
        "status": "started",
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    job_result = final_state.get("job_result")
    assert job_result is not None
    assert job_result.status == "rejected"


# Case I: Too many audio files -> 400
def test_case_i_too_many_audio_files(monkeypatch):
    """Case I: Submitting 4 audio files when MAX_AUDIO_SOURCES_PER_JOB=3 -> 400."""
    client = TestClient(app)
    monkeypatch.setattr(settings, "MAX_AUDIO_SOURCES_PER_JOB", 3)

    files = [
        ("files", ("m1.mp3", b"ID3\x031", "audio/mpeg")),
        ("files", ("m2.mp3", b"ID3\x032", "audio/mpeg")),
        ("files", ("m3.mp3", b"ID3\x033", "audio/mpeg")),
        ("files", ("m4.mp3", b"ID3\x034", "audio/mpeg")),
    ]
    resp = client.post("/process", files=files)
    assert resp.status_code == 400
    assert "Too many audio files" in resp.json()["detail"]


# Case J: Aggregate audio duration exceeded -> rejected
def test_case_j_aggregate_audio_duration_exceeded(monkeypatch):
    """Case J: Total duration across audio files exceeds MAX_TOTAL_AUDIO_DURATION_SECONDS -> 400."""
    client = TestClient(app)
    monkeypatch.setattr(settings, "MAX_TOTAL_AUDIO_DURATION_SECONDS", 100)
    from app.services.source_processing import audio as audio_service
    monkeypatch.setattr(audio_service, "get_audio_duration_seconds", lambda *args, **kwargs: 60.0)

    files = [
        ("files", ("m1.mp3", b"ID3\x031", "audio/mpeg")),
        ("files", ("m2.mp3", b"ID3\x032", "audio/mpeg")),
    ]
    resp = client.post("/process", files=files)
    assert resp.status_code == 400
    assert "Aggregate audio duration" in resp.json()["detail"]


# Case K: Aggregate upload bytes exceeded -> 413
def test_case_k_aggregate_upload_bytes_exceeded(monkeypatch):
    """Case K: Total bytes across all uploads exceeds MAX_TOTAL_UPLOAD_BYTES -> 413."""
    client = TestClient(app)
    monkeypatch.setattr(settings, "MAX_TOTAL_UPLOAD_BYTES", 1000)

    files = [
        ("files", ("big1.txt", b"A" * 600, "text/plain")),
        ("files", ("big2.txt", b"B" * 600, "text/plain")),
    ]
    resp = client.post("/process", files=files)
    assert resp.status_code == 413
    assert "Aggregate upload size exceeds limit" in resp.json()["detail"]


# Case L: Duplicate document IDs -> 400
def test_case_l_duplicate_document_ids():
    """Case L: Supplying duplicate document IDs -> 400."""
    client = TestClient(app)
    files = [
        ("files", ("a.txt", b"Some text content", "text/plain")),
        ("files", ("b.txt", b"Other text content", "text/plain")),
    ]
    data = {"document_ids": ["dup_id", "dup_id"]}
    resp = client.post("/process", files=files, data=data)
    assert resp.status_code == 400
    assert "unique document ID" in resp.json()["detail"]


# Case M: Chunk ID collision regression
def test_case_m_chunk_id_collision_regression():
    """Case M: Audio chunks from two distinct sources must not produce identical chunk IDs."""
    chunks_aud1 = [
        SourceChunk(chunk_id="tmp1", text="First audio chunk", start_char=0, end_char=17, start_time_sec=0.0, end_time_sec=5.0)
    ]
    chunks_aud2 = [
        SourceChunk(chunk_id="tmp2", text="Second audio chunk", start_char=0, end_char=18, start_time_sec=0.0, end_time_sec=5.0)
    ]

    windows_aud1 = reconstruct_audio_chunks(chunks_aud1, job_id="job_test", document_id="meeting_product", default_language="en")
    windows_aud2 = reconstruct_audio_chunks(chunks_aud2, job_id="job_test", document_id="meeting_technical", default_language="en")

    assert len(windows_aud1) == 1
    assert len(windows_aud2) == 1
    assert windows_aud1[0].chunk_id != windows_aud2[0].chunk_id
    assert "meeting_product" in windows_aud1[0].chunk_id
    assert "meeting_technical" in windows_aud2[0].chunk_id


# Case N: PostgreSQL persistence (chunks from 2 audio files in same job)
@pytest.mark.asyncio
async def test_case_n_postgresql_chunk_persistence():
    """Case N: Persist chunks from multiple audio files into the store without unique constraint violations."""
    stores = get_stores()
    job_id = "test_pg_multi_audio_chunks"

    records = [
        SourceChunkRecord(
            job_id=job_id,
            chunk_id=f"trans_{job_id}_audio1_semantic_0",
            source_document_id="audio1",
            chunk_index=0,
            text="First recording text",
            start_time_sec=0.0,
            end_time_sec=10.0,
        ),
        SourceChunkRecord(
            job_id=job_id,
            chunk_id=f"trans_{job_id}_audio2_semantic_0",
            source_document_id="audio2",
            chunk_index=1,
            text="Second recording text",
            start_time_sec=0.0,
            end_time_sec=10.0,
        ),
    ]

    await stores.chunks.save_chunks(records)
    loaded = await stores.chunks.get_chunks(job_id)
    assert len(loaded) == 2
    loaded_ids = {c.chunk_id for c in loaded}
    assert loaded_ids == {f"trans_{job_id}_audio1_semantic_0", f"trans_{job_id}_audio2_semantic_0"}


# Case O: Worker reconstruction (audio1, audio2, pdf, docx)
@pytest.mark.asyncio
async def test_case_o_worker_reconstruction_multi_audio():
    """Case O: Worker properly reconstructs state with 2 audio files + 2 docs."""
    stores = get_stores()
    job = AiJobRecord(
        job_id="job_worker_recon_4",
        tenant_id="tenant_a",
        project_id="proj_a",
        input_type=InputType.BACKEND_SOURCES.value,
        requested_by="admin",
    )
    await stores.jobs.create_job(job)

    db_docs = [
        SourceDocumentRecord(job_id=job.job_id, backend_document_id="aud_1", file_name="meeting1.mp3", source_type="audio", mime_type="audio/mpeg", sha256_hash="h1"),
        SourceDocumentRecord(job_id=job.job_id, backend_document_id="aud_2", file_name="meeting2.mp3", source_type="audio", mime_type="audio/mpeg", sha256_hash="h2"),
        SourceDocumentRecord(job_id=job.job_id, backend_document_id="pdf_1", file_name="spec.pdf", source_type="pdf", mime_type="application/pdf", sha256_hash="h3"),
        SourceDocumentRecord(job_id=job.job_id, backend_document_id="docx_1", file_name="notes.docx", source_type="docx", mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", sha256_hash="h4"),
    ]
    await stores.chunks.save_documents(db_docs)

    class MultiSourceClient:
        async def fetch_document_bytes(self, ref):
            doc_id = ref.get("document_id")
            if doc_id == "aud_1": return b"ID3\x03audio1"
            if doc_id == "aud_2": return b"ID3\x03audio2"
            if doc_id == "pdf_1": return _make_sample_pdf()
            if doc_id == "docx_1": return _make_sample_docx()
            return b""

    state = await build_worker_initial_state(job, stores, backend_client=MultiSourceClient())
    assert state["job_id"] == "job_worker_recon_4"
    assert state["file_type"] == "sources"
    assert len(state["raw_inputs"]) == 4
    input_types = {item["document_id"]: item["file_type"] for item in state["raw_inputs"]}
    assert input_types == {"aud_1": "audio", "aud_2": "audio", "pdf_1": "pdf", "docx_1": "docx"}


# Case P: Idempotency (reordered sources & changed audio content)
def test_case_p_idempotency_multi_audio():
    """Case P: Order insensitive fingerprinting and content sensitivity."""
    req_a = CreateJobRequest(
        job_id="job_idemp_1",
        project_id="proj_1",
        input_type="backend_sources",
        source_documents=[
            SourceDocumentIn(document_id="a1", filename="a.mp3", file_type="audio", sha256_hash="hash_a"),
            SourceDocumentIn(document_id="a2", filename="b.mp3", file_type="audio", sha256_hash="hash_b"),
            SourceDocumentIn(document_id="d1", filename="c.pdf", file_type="pdf", sha256_hash="hash_c"),
        ],
        options=JobOptionsIn(language="en"),
    )
    req_b = CreateJobRequest(
        job_id="job_idemp_2",
        project_id="proj_1",
        input_type="backend_sources",
        source_documents=[
            SourceDocumentIn(document_id="d1", filename="c.pdf", file_type="pdf", sha256_hash="hash_c"),
            SourceDocumentIn(document_id="a2", filename="b.mp3", file_type="audio", sha256_hash="hash_b"),
            SourceDocumentIn(document_id="a1", filename="a.mp3", file_type="audio", sha256_hash="hash_a"),
        ],
        options=JobOptionsIn(language="en"),
    )
    assert compute_job_request_fingerprint(req_a) == compute_job_request_fingerprint(req_b)

    req_modified_audio = CreateJobRequest(
        job_id="job_idemp_3",
        project_id="proj_1",
        input_type="backend_sources",
        source_documents=[
            SourceDocumentIn(document_id="a1", filename="a.mp3", file_type="audio", sha256_hash="hash_a_NEW"),
            SourceDocumentIn(document_id="a2", filename="b.mp3", file_type="audio", sha256_hash="hash_b"),
            SourceDocumentIn(document_id="d1", filename="c.pdf", file_type="pdf", sha256_hash="hash_c"),
        ],
        options=JobOptionsIn(language="en"),
    )
    assert compute_job_request_fingerprint(req_a) != compute_job_request_fingerprint(req_modified_audio)
