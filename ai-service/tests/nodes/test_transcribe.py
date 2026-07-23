import os
import sys
import time
import pytest
import shutil
from unittest.mock import patch, MagicMock, AsyncMock
from dotenv import load_dotenv

load_dotenv()

from app.nodes.transcribe import transcribe_node, _transcribe_groq, _transcribe_deepgram
from app.schemas.items import SourceChunk

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _has_key(env_var: str) -> bool:
    val = os.getenv(env_var, "")
    return bool(val and "your_" not in val)

def _make_state(raw_bytes=None, file_type="audio"):
    return {
        "job_id": "test-job-transcribe",
        "raw_bytes": raw_bytes,
        "file_type": file_type,
        "raw_text": None,
        "chunks": [],
        "error": None,
        "is_useful": True,
        "relevance_score": 1.0,
        "status": "started",
        "metadata": {"filename": "test.mp3"}
    }

# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests (no real API calls)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_audio_bytes():
    """Guard clause: missing raw_bytes returns error."""
    state = _make_state(raw_bytes=None)
    with patch("app.nodes.transcribe._validate_ffmpeg", return_value=None):
        result = await transcribe_node(state)

    assert result["raw_text"] is None
    assert result["chunks"] == []
    assert "TRANSCRIBE_NO_BYTES" in result["error"]

@pytest.mark.asyncio
async def test_ffmpeg_missing_failure():
    """Verify node fails if ffmpeg is missing."""
    state = _make_state(raw_bytes=b"fake-audio")
    with patch("shutil.which", return_value=None):
        result = await transcribe_node(state)
        
    assert result["status"] == "failed_system_dependency"
    assert "ffmpeg" in result["error"]

@pytest.mark.asyncio
async def test_fallback_on_bad_groq_key(monkeypatch):
    """
    When the primary provider fails, automatically attempt the fallback.
    """
    monkeypatch.setenv("TRANSCRIBE_PROVIDER", "groq")

    async def _bad_groq(*args, **kwargs):
        raise Exception("Simulated Groq outage")

    async def _good_deepgram(*args, **kwargs):
        return "Deepgram text", [SourceChunk(chunk_id="c1", text="Deepgram text", start_char=0, end_char=0)]

    with patch("app.nodes.transcribe._validate_ffmpeg", return_value=None), \
         patch("app.nodes.transcribe._transcribe_groq", side_effect=_bad_groq), \
         patch("app.nodes.transcribe._transcribe_deepgram", side_effect=_good_deepgram):

        state = _make_state(raw_bytes=b"fake-audio-bytes")
        result = await transcribe_node(state)

    assert result["raw_text"] == "Deepgram text"
    assert len(result["chunks"]) == 1
    assert "TRANSCRIBE_GROQ_FAILURE" in result["error"]
    assert result["status"] == "completed_via_fallback"

@pytest.mark.asyncio
async def test_groq_chunking_and_mapping(monkeypatch):
    """Verify Groq segments are mapped to SourceChunk."""
    mock_groq = MagicMock()
    mock_client = MagicMock()
    mock_groq.AsyncGroq.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.segments = [
        {"text": "Hello world", "start": 0.0, "end": 1.5},
        {"text": "Testing 123", "start": 2.0, "end": 4.0}
    ]
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_response)
    
    with patch.dict('sys.modules', {'groq': mock_groq}), \
         patch("app.nodes.transcribe.settings") as mock_settings:
        mock_settings.GROQ_API_KEY = "fake"
        mock_settings.GROQ_WHISPER_MODEL = "whisper-v3"
        mock_settings.GROQ_LANGUAGE = "en"
        
        text, chunks = await _transcribe_groq(b"fake-bytes", "mp3", job_id="test")
        
        assert "Hello world" in text
        assert len(chunks) == 2
        assert chunks[0].text == "Hello world"
        assert chunks[0].start_time_sec == 0.0
        assert chunks[0].chunk_id.startswith("trans_test_groq")
        assert chunks[0].language == "en"

@pytest.mark.asyncio
async def test_deepgram_bilingual_mapping(monkeypatch):
    """Verify Deepgram bilingual merge produces chunks."""
    ar_data = {
        "results": {
            "utterances": [
                {"transcript": "مرحبا", "start": 0.0, "end": 2.0, "confidence": 0.8, "speaker": 0}
            ]
        }
    }
    en_data = {
        "results": {
            "utterances": [
                {"transcript": "Hello", "start": 0.5, "end": 1.5, "confidence": 0.95, "speaker": 0}
            ]
        }
    }
    
    mock_ar_resp = MagicMock()
    mock_ar_resp.status_code = 200
    mock_ar_resp.json.return_value = ar_data
    
    mock_en_resp = MagicMock()
    mock_en_resp.status_code = 200
    mock_en_resp.json.return_value = en_data
    
    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(side_effect=[mock_ar_resp, mock_en_resp])
    mock_client_instance.__aenter__.return_value = mock_client_instance
    
    with patch("httpx.AsyncClient", return_value=mock_client_instance), \
         patch("app.nodes.transcribe.settings") as mock_settings:
        mock_settings.DEEPGRAM_API_KEY = "fake"
        
        text, chunks = await _transcribe_deepgram(b"fake", "mp3", job_id="test", language="mixed")
        
        assert "Hello" in text
        assert len(chunks) == 1
        assert chunks[0].text == "Hello"
        assert chunks[0].speaker == "0"
        assert chunks[0].language == "en"
        assert chunks[0].asr_confidence == pytest.approx(0.95)
