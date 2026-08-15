import io
import wave
import pytest
from app.config import settings
from app.services.source_processing.audio import get_audio_duration_seconds, process_audio_source
from app.services.source_processing.models import SourceInput


def create_synthetic_wav(duration_seconds: float, sample_rate: int = 8000) -> bytes:
    """Create a valid synthetic in-memory PCM WAV audio buffer."""
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        num_frames = int(duration_seconds * sample_rate)
        w.writeframes(b"\x00\x00" * num_frames)
    return bio.getvalue()


def test_get_audio_duration_seconds():
    # 5-second audio
    wav_5s = create_synthetic_wav(5.0)
    dur = get_audio_duration_seconds(wav_5s, "wav")
    assert dur is not None
    assert abs(dur - 5.0) < 0.1


@pytest.mark.asyncio
async def test_process_audio_source_rejects_over_duration(monkeypatch):
    wav_10s = create_synthetic_wav(10.0)
    # Temporarily set max audio duration to 3 seconds
    monkeypatch.setattr(settings, "MAX_AUDIO_DURATION_SECONDS", 3)

    source = SourceInput(
        document_id="audio_test_1",
        filename="long_recording.wav",
        file_type="audio",
        raw_bytes=wav_10s,
        audio_format="wav",
    )

    result = await process_audio_source(source, job_id="job_duration_test")
    assert result.status == "failed"
    assert result.error_code == "AUDIO_DURATION_EXCEEDED"
    assert "exceeds maximum allowed limit" in result.error_message


@pytest.mark.asyncio
async def test_process_audio_source_allows_within_duration(monkeypatch):
    wav_2s = create_synthetic_wav(2.0)
    monkeypatch.setattr(settings, "MAX_AUDIO_DURATION_SECONDS", 30)

    # Mock ffmpeg validation, groq transcription, and relevance check so test runs deterministically
    import app.services.source_processing.audio as spa
    monkeypatch.setattr(spa, "_validate_ffmpeg", lambda: None)
    async def _mock_groq(*args, **kwargs):
        from app.schemas.items import SourceChunk
        c = SourceChunk(chunk_id="chunk-1", text="The user must be able to reset password.", start_char=0, end_char=40)
        return ("The user must be able to reset password.", [c])
    monkeypatch.setattr(spa, "_transcribe_groq", _mock_groq)
    async def _mock_relevance(*args, **kwargs):
        return type("RelevanceRes", (), {"is_useful": True, "reason": "Relevant", "relevance_score": 0.95})()
    monkeypatch.setattr(spa, "_run_relevance_check", _mock_relevance)

    source = SourceInput(
        document_id="audio_test_2",
        filename="short_recording.wav",
        file_type="audio",
        raw_bytes=wav_2s,
        audio_format="wav",
    )

    result = await process_audio_source(source, job_id="job_duration_test_2")
    assert result.status == "ready"
    assert len(result.chunks) > 0
