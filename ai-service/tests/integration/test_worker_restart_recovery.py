import pytest
from app.config import settings
from app.store.factory import get_stores
from app.store.models import AiJobRecord, JobStatus, JobOptions, InputType
from app.worker.state import stash_input, build_worker_initial_state, load_input
from app.services.source_processing.audio import process_audio_source
from app.services.source_processing.models import SourceInput


@pytest.mark.asyncio
async def test_scenario_c_worker_recovery_from_redis_cache(monkeypatch):
    """Scenario C: Worker seamlessly recovers mixed source state from Redis input cache."""
    job_id = "worker-recov-cache-1"
    stores = get_stores()

    storage = {}
    class FakeRedis:
        def set(self, key, value, ex=None):
            storage[key] = value
            return True
        def get(self, key):
            return storage.get(key)

    fake_client = FakeRedis()
    import app.queue.redis_queue as rq
    monkeypatch.setattr(rq, "get_redis_connection", lambda *args, **kwargs: fake_client)

    # Stash mixed input into cache
    stash_input(
        job_id,
        raw_inputs=[
            {"document_id": "d1", "filename": "spec.pdf", "raw_bytes": b"%PDF-1.4\ncontent", "file_type": "pdf"},
            {"document_id": "d2", "filename": "notes.txt", "raw_bytes": b"plain text", "file_type": "text"},
        ],
        file_type="sources",
        language="en",
    )

    job = AiJobRecord(
        job_id=job_id,
        tenant_id="tenant_1",
        project_id="proj_1",
        input_type=InputType.BACKEND_SOURCES.value,
        status=JobStatus.QUEUED,
        options=JobOptions(),
    )

    initial_state = await build_worker_initial_state(job, stores)
    assert initial_state["job_id"] == job_id
    assert len(initial_state["raw_inputs"]) == 2
    assert initial_state["raw_inputs"][0]["document_id"] == "d1"
    assert initial_state["raw_inputs"][1]["document_id"] == "d2"


@pytest.mark.asyncio
async def test_scenario_d_stt_primary_failure_fallback_success(monkeypatch):
    """Scenario D: Primary STT provider fails; fallback provider succeeds and warning is recorded."""
    monkeypatch.setattr(settings, "TRANSCRIBE_PROVIDER", "groq")
    monkeypatch.setattr(settings, "DEEPGRAM_API_KEY", "mock_dg_key")

    import app.services.source_processing.audio as spa
    monkeypatch.setattr(spa, "_validate_ffmpeg", lambda: None)

    # Primary (Groq) fails with simulated connection error
    async def _failing_groq(*args, **kwargs):
        raise ConnectionError("Groq Whisper API unreachable")
    monkeypatch.setattr(spa, "_transcribe_groq", _failing_groq)

    # Fallback (Deepgram) succeeds
    async def _success_deepgram(*args, **kwargs):
        from app.schemas.items import SourceChunk
        c = SourceChunk(chunk_id="c_fb_1", text="Fallback transcribed content.", start_char=0, end_char=28)
        return ("Fallback transcribed content.", [c])
    monkeypatch.setattr(spa, "_transcribe_deepgram", _success_deepgram)
    async def _mock_relevance(*args, **kwargs):
        return type("RelevanceRes", (), {"is_useful": True, "reason": "Relevant", "relevance_score": 0.95})()
    monkeypatch.setattr(spa, "_run_relevance_check", _mock_relevance)

    source = SourceInput(
        document_id="audio_fb_1",
        filename="meeting.mp3",
        file_type="audio",
        raw_bytes=b"ID3\x03fake-audio-bytes",
        audio_format="mp3",
    )

    result = await process_audio_source(source, job_id="job_fallback_test")
    assert result.status == "ready"
    assert result.warning_code == "STT_FALLBACK_USED"
    assert "successfully used fallback" in result.warning_message
