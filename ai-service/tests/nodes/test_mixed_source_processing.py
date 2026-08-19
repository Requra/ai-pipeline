import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from app.schemas.items import SourceChunk
from app.nodes.prepare_sources import prepare_sources_node
from app.services.source_processing.models import SourceInput, ProcessedSource
from app.services.source_processing.document import process_document_source
from app.services.source_processing.audio import process_audio_source
from app.nodes.ingest import RelevanceCheck


@pytest.fixture(autouse=True)
def _mock_relevance(monkeypatch):
    from app.nodes import ingest
    from app.services.source_processing import document, audio
    async def fake_relevance(text: str) -> RelevanceCheck:
        return RelevanceCheck(is_useful=True, relevance_score=0.95, reason="software requirements present")
    monkeypatch.setattr(ingest, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(document, "_run_relevance_check", fake_relevance)
    monkeypatch.setattr(audio, "_run_relevance_check", fake_relevance)


@pytest.mark.asyncio
async def test_process_document_source_provenance():
    text_bytes = b"The system shall authenticate users using OAuth 2.0 with JWT tokens."
    source = SourceInput(
        document_id="doc_specs",
        filename="specs.txt",
        file_type="text",
        raw_bytes=text_bytes,
    )
    res = await process_document_source(source, job_id="job-1")
    assert res.status == "ready"
    assert res.is_useful is True
    assert len(res.chunks) >= 1
    for chunk in res.chunks:
        assert chunk.document_id == "doc_specs"
        assert chunk.start_char is not None
        assert chunk.end_char is not None


@pytest.mark.asyncio
async def test_process_audio_source_provenance(monkeypatch):
    from app.services.source_processing import audio

    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)

    async def fake_transcribe(*args, **kwargs):
        utterances = [
            {
                "speaker": "Alice",
                "start": 0.0,
                "end": 2.5,
                "text": "We need the payment gateway to retry failed transactions up to three times.",
                "confidence": 0.95,
            }
        ]
        return "We need the payment gateway to retry failed transactions up to three times.", utterances

    monkeypatch.setattr(audio, "_transcribe_groq", fake_transcribe)

    source = SourceInput(
        document_id="audio_meeting_1",
        filename="meeting.mp3",
        file_type="audio",
        raw_bytes=b"ID3\x03fake-audio-bytes",
        audio_format="mp3",
    )
    res = await process_audio_source(source, job_id="job-2")
    assert res.status == "ready"
    assert res.is_useful is True
    assert len(res.chunks) == 1
    assert res.chunks[0].document_id == "audio_meeting_1"
    assert res.chunks[0].speaker == "Alice"
    assert res.chunks[0].start_time_sec == 0.0
    assert res.chunks[0].end_time_sec == 2.5


@pytest.mark.asyncio
async def test_prepare_sources_node_mixed_pdf_and_audio(monkeypatch):
    from app.services.source_processing import audio

    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)

    async def fake_transcribe(*args, **kwargs):
        utterances = [
            {
                "speaker": "ProductOwner",
                "start": 0.0,
                "end": 3.0,
                "text": "The user should be able to export reports as Excel spreadsheets.",
                "confidence": 0.98,
            }
        ]
        return "The user should be able to export reports as Excel spreadsheets.", utterances

    monkeypatch.setattr(audio, "_transcribe_groq", fake_transcribe)

    state = {
        "job_id": "mixed-job-1",
        "language": "en",
        "raw_inputs": [
            {
                "document_id": "doc_pdf_1",
                "filename": "requirements.txt",
                "file_type": "text",
                "raw_bytes": b"Requirement: The system shall provide secure password reset via email link.",
            },
            {
                "document_id": "audio_meeting_1",
                "filename": "sprint_planning.mp3",
                "file_type": "audio",
                "audio_format": "mp3",
                "raw_bytes": b"ID3\x03audiobytes",
            },
        ],
        "source_documents": [
            {"document_id": "doc_pdf_1", "filename": "requirements.txt", "file_type": "text"},
            {"document_id": "audio_meeting_1", "filename": "sprint_planning.mp3", "file_type": "audio"},
        ],
        "warnings": [],
    }

    result = await prepare_sources_node(state)
    assert result["status"] == "sources_prepared"
    assert result["is_useful"] is True
    assert result["partial_source_failure"] is False
    assert len(result["chunks"]) >= 2

    # Verify provenance across heterogeneous sources
    doc_ids = {chunk.document_id for chunk in result["chunks"]}
    assert doc_ids == {"doc_pdf_1", "audio_meeting_1"}

    audio_chunks = [c for c in result["chunks"] if c.document_id == "audio_meeting_1"]
    assert len(audio_chunks) == 1
    assert audio_chunks[0].speaker == "ProductOwner"
    assert audio_chunks[0].start_time_sec == 0.0

    doc_chunks = [c for c in result["chunks"] if c.document_id == "doc_pdf_1"]
    assert len(doc_chunks) >= 1
    assert doc_chunks[0].start_char is not None


@pytest.mark.asyncio
async def test_prepare_sources_node_partial_failure_continues(monkeypatch):
    from app.services.source_processing import audio

    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)

    async def fail_transcribe(*args, **kwargs):
        raise RuntimeError("STT transcription network timeout")

    monkeypatch.setattr(audio, "_transcribe_groq", fail_transcribe)
    monkeypatch.setattr(audio, "_transcribe_deepgram", fail_transcribe)

    state = {
        "job_id": "mixed-partial-job",
        "language": "en",
        "raw_inputs": [
            {
                "document_id": "doc_1",
                "filename": "system_specs.txt",
                "file_type": "text",
                "raw_bytes": b"The API shall enforce a rate limit of 100 requests per minute per IP address.",
            },
            {
                "document_id": "audio_corrupt",
                "filename": "corrupt_recording.mp3",
                "file_type": "audio",
                "audio_format": "mp3",
                "raw_bytes": b"ID3\x03corruptdata",
            },
        ],
        "source_documents": [
            {"document_id": "doc_1", "filename": "system_specs.txt", "file_type": "text"},
            {"document_id": "audio_corrupt", "filename": "corrupt_recording.mp3", "file_type": "audio"},
        ],
        "warnings": [],
    }

    result = await prepare_sources_node(state)
    assert result["status"] == "sources_prepared"
    assert result["is_useful"] is True
    assert result["partial_source_failure"] is True
    assert len(result["chunks"]) >= 1
    assert all(c.document_id == "doc_1" for c in result["chunks"])

    warning_codes = [w["code"] for w in result["warnings"]]
    assert "SOURCE_PROCESSING_FAILED" in warning_codes
    assert "PARTIAL_SOURCE_FAILURE" in warning_codes


@pytest.mark.asyncio
async def test_prepare_sources_all_failed(monkeypatch):
    from app.services.source_processing import audio

    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)

    async def fail_transcribe(*args, **kwargs):
        raise RuntimeError("All providers unavailable")

    monkeypatch.setattr(audio, "_transcribe_groq", fail_transcribe)
    monkeypatch.setattr(audio, "_transcribe_deepgram", fail_transcribe)

    state = {
        "job_id": "mixed-all-failed",
        "language": "en",
        "raw_inputs": [
            {
                "document_id": "doc_empty",
                "filename": "empty.pdf",
                "file_type": "pdf",
                "raw_bytes": b"%PDF-1.4 empty corrupted",
            },
            {
                "document_id": "audio_fail",
                "filename": "audio.mp3",
                "file_type": "audio",
                "audio_format": "mp3",
                "raw_bytes": b"ID3\x03data",
            },
        ],
        "source_documents": [
            {"document_id": "doc_empty", "filename": "empty.pdf", "file_type": "pdf"},
            {"document_id": "audio_fail", "filename": "audio.mp3", "file_type": "audio"},
        ],
        "warnings": [],
    }

    result = await prepare_sources_node(state)
    assert result["status"] == "failed"
    assert result["is_useful"] is False
    assert "ALL_SOURCES_FAILED" in result["error"]
    assert len(result["chunks"]) == 0


@pytest.mark.asyncio
async def test_prepare_sources_stt_fallback_used(monkeypatch):
    from app.services.source_processing import audio

    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)

    async def fail_groq(*args, **kwargs):
        raise RuntimeError("Groq quota exceeded")

    async def succeed_deepgram(*args, **kwargs):
        utterances = [
            {
                "speaker": "DevLead",
                "start": 0.0,
                "end": 2.0,
                "text": "We need to log all transaction attempts with structured JSON format.",
                "confidence": 0.92,
            }
        ]
        return "We need to log all transaction attempts with structured JSON format.", utterances

    monkeypatch.setattr(audio, "_transcribe_groq", fail_groq)
    monkeypatch.setattr(audio, "_transcribe_deepgram", succeed_deepgram)

    state = {
        "job_id": "mixed-fallback-job",
        "language": "en",
        "raw_inputs": [
            {
                "document_id": "audio_source_1",
                "filename": "standup.mp3",
                "file_type": "audio",
                "audio_format": "mp3",
                "raw_bytes": b"ID3\x03audiobytes",
            }
        ],
        "source_documents": [
            {"document_id": "audio_source_1", "filename": "standup.mp3", "file_type": "audio"}
        ],
        "warnings": [],
    }

    result = await prepare_sources_node(state)
    assert result["status"] == "sources_prepared"
    assert result["is_useful"] is True
    assert len(result["chunks"]) == 1
    assert result["chunks"][0].document_id == "audio_source_1"

    warning_codes = [w["code"] for w in result["warnings"]]
    assert "STT_FALLBACK_USED" in warning_codes
