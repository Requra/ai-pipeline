import pytest
from app.config import settings
from app.api.internal import BoundedInMemoryStore
from app.worker.state import _input_ttl_seconds, build_worker_initial_state
from app.clients.backend import SourceUnavailableError
from app.store.models import AiJobRecord, InputType, JobStatus, JobOptions
from app.store.factory import get_stores


def test_bounded_in_memory_store_eviction():
    store = BoundedInMemoryStore(max_items=3)
    store["k1"] = b"data1"
    store["k2"] = b"data2"
    store["k3"] = b"data3"

    assert "k1" in store
    assert "k2" in store
    assert "k3" in store

    # Add 4th item -> k1 must be evicted
    store["k4"] = b"data4"
    assert len(store._data) == 3
    assert "k1" not in store
    assert "k4" in store


def test_bounded_in_memory_store_disabled_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    store = BoundedInMemoryStore(max_items=5)
    store["k1"] = b"data1"

    # Must not store in memory in production
    assert "k1" not in store
    assert store.get("k1") is None


def test_input_ttl_configurable(monkeypatch):
    monkeypatch.setattr(settings, "INPUT_CACHE_TTL_SECONDS", 1234)
    assert _input_ttl_seconds() == 1234


@pytest.mark.asyncio
async def test_worker_initial_state_raises_explicit_error_on_missing_sources():
    stores = get_stores()
    job = AiJobRecord(
        job_id="job_missing_sources_1",
        tenant_id="t1",
        project_id="p1",
        input_type=InputType.BACKEND_SOURCES.value,
        status=JobStatus.QUEUED,
        options=JobOptions(),
    )

    with pytest.raises(SourceUnavailableError) as exc_info:
        await build_worker_initial_state(job, stores, backend_client=None)

    assert "Original source bytes/text could not be recovered" in str(exc_info.value)
