"""Tests for the in-memory store implementations (default dev/test backend)."""

from __future__ import annotations

import asyncio

import pytest

from app.store.memory import (
    MemoryChunkStore,
    MemoryEmbeddingStore,
    MemoryJobStore,
    MemoryResultStore,
)
from app.store.models import (
    AiJobRecord,
    ChunkEmbeddingRecord,
    JobOptions,
    JobStatus,
    RETRYABLE_JOB_STATUSES,
    SourceChunkRecord,
    SourceDocumentRecord,
)

pytestmark = pytest.mark.asyncio


async def test_job_store_persists_and_loads_status():
    store = MemoryJobStore()
    await store.create_job(AiJobRecord(job_id="j1", tenant_id="t1", project_id="p1"))

    rec = await store.get_job("j1")
    assert rec is not None and rec.status == JobStatus.QUEUED

    await store.set_status("j1", JobStatus.PROCESSING, current_node="extract", progress_pct=45)
    rec = await store.get_job("j1")
    assert rec.status == JobStatus.PROCESSING
    assert rec.current_node == "extract" and rec.progress_pct == 45
    assert rec.started_at is not None  # PROCESSING stamps started_at

    # Public status view maps PARTIAL/REJECTED to COMPLETED, floats for times.
    await store.set_status("j1", JobStatus.PARTIAL)
    view = (await store.get_job("j1")).to_status_view(result={"x": 1})
    assert view["status"] == "COMPLETED"
    assert isinstance(view["created_at"], float)
    assert view["result"] == {"x": 1}


async def test_job_store_cancellation_flag():
    store = MemoryJobStore()
    await store.create_job(AiJobRecord(job_id="j2"))
    assert await store.is_cancel_requested("j2") is False
    await store.request_cancel("j2")
    assert await store.is_cancel_requested("j2") is True


async def test_job_store_events_and_attempts():
    from app.store.models import JobAttemptRecord, JobEventRecord

    store = MemoryJobStore()
    await store.create_job(AiJobRecord(job_id="j3"))
    await store.add_event(JobEventRecord(job_id="j3", event_type="job_started"))
    await store.add_event(JobEventRecord(job_id="j3", event_type="warn", severity="warning"))
    await store.add_attempt(JobAttemptRecord(job_id="j3", attempt_number=1))
    events = await store.list_events("j3")
    assert [e.event_type for e in events] == ["job_started", "warn"]
    assert len(await store.list_attempts("j3")) == 1


async def test_result_store_roundtrip():
    store = MemoryResultStore()
    payload = {"job_id": "j4", "status": "completed", "exports": {"excel": {"available": True}}}
    await store.save_result("j4", payload, status="completed", processing_time_ms=42)
    assert await store.get_result("j4") == payload
    rec = await store.get_result_record("j4")
    assert rec.processing_time_ms == 42 and rec.exports_json == {"excel": {"available": True}}
    assert await store.get_result("missing") is None


async def test_chunk_store_persist_and_scoped_find():
    store = MemoryChunkStore()
    docs = await store.save_documents(
        [SourceDocumentRecord(job_id="j5", tenant_id="t1", project_id="p1", file_name="d.txt")]
    )
    assert docs[0].id is not None

    await store.save_chunks(
        [
            SourceChunkRecord(job_id="j5", tenant_id="t1", project_id="p1", chunk_id="c1", text="a"),
            SourceChunkRecord(job_id="j5", tenant_id="t1", project_id="p1", chunk_id="c2", text="b"),
            SourceChunkRecord(job_id="j6", tenant_id="t2", project_id="p9", chunk_id="c3", text="c"),
        ]
    )
    assert len(await store.get_chunks("j5")) == 2
    # Scoped find never returns another tenant's chunk.
    scoped = await store.find_chunks(tenant_id="t1", project_id="p1")
    assert {c.chunk_id for c in scoped} == {"c1", "c2"}


async def test_embedding_store_vector_search_is_tenant_scoped():
    store = MemoryEmbeddingStore()
    await store.save_embeddings(
        [
            ChunkEmbeddingRecord(chunk_id="c1", job_id="j1", tenant_id="t1", project_id="p1",
                                 embedding_model="m", embedding=[1.0, 0.0]),
            ChunkEmbeddingRecord(chunk_id="c2", job_id="j1", tenant_id="t1", project_id="p1",
                                 embedding_model="m", embedding=[0.0, 1.0]),
            ChunkEmbeddingRecord(chunk_id="c3", job_id="jX", tenant_id="OTHER", project_id="pX",
                                 embedding_model="m", embedding=[1.0, 0.0]),
        ]
    )
    assert await store.count_for_job("j1") == 2

    hits = await store.vector_search([1.0, 0.05], tenant_id="t1", project_id="p1", top_k=5)
    ids = [h["chunk_id"] for h in hits]
    assert ids[0] == "c1"                 # most similar first
    assert "c3" not in ids                # never leaks the other tenant

    # Querying as the other tenant cannot see t1's chunks.
    other = await store.vector_search([1.0, 0.0], tenant_id="t1", project_id="p1", top_k=5)
    assert all(h["chunk_id"] != "c3" for h in other)

    with pytest.raises(ValueError, match="requires job_id"):
        await store.vector_search([1.0, 0.0])


async def test_memory_artifact_saves_replace_prior_attempt_data():
    chunks = MemoryChunkStore()
    embeddings = MemoryEmbeddingStore()

    await chunks.save_chunks(
        [SourceChunkRecord(job_id="retry-1", chunk_id="old", text="old")]
    )
    await chunks.save_chunks(
        [SourceChunkRecord(job_id="retry-1", chunk_id="new", text="new")]
    )
    assert [c.chunk_id for c in await chunks.get_chunks("retry-1")] == ["new"]

    await embeddings.save_embeddings(
        [
            ChunkEmbeddingRecord(
                job_id="retry-1",
                chunk_id="old",
                embedding_model="test",
                embedding=[1.0, 0.0],
            )
        ]
    )
    await embeddings.save_embeddings(
        [
            ChunkEmbeddingRecord(
                job_id="retry-1",
                chunk_id="new",
                embedding_model="test",
                embedding=[0.0, 1.0],
            )
        ]
    )
    assert await embeddings.count_for_job("retry-1") == 1


# ---------------------------------------------------------------------------
# create_or_get / mark_duplicate / try_requeue_for_retry (idempotency)
# ---------------------------------------------------------------------------

async def test_create_or_get_creates_once_and_returns_existing_after():
    store = MemoryJobStore()
    rec_a = AiJobRecord(job_id="j10", tenant_id="t1", project_id="p1", request_fingerprint="fp-1")
    row, created = await store.create_or_get(rec_a)
    assert created is True and row.job_id == "j10"

    rec_b = AiJobRecord(job_id="j10", tenant_id="t1", project_id="p1", request_fingerprint="fp-1")
    row2, created2 = await store.create_or_get(rec_b)
    assert created2 is False
    assert row2.request_fingerprint == "fp-1"  # the original winner's row, unmodified


async def test_create_or_get_concurrent_same_job_id_creates_exactly_one_row():
    """Two concurrent create_or_get calls for the same new job_id: exactly one
    creates, the other observes the winner's row. No duplicate row/attempt."""
    store = MemoryJobStore()

    def _candidate():
        return AiJobRecord(job_id="race-job", tenant_id="t1", project_id="p1", request_fingerprint="fp-x")

    results = await asyncio.gather(
        store.create_or_get(_candidate()), store.create_or_get(_candidate())
    )
    created_flags = sorted(r[1] for r in results)
    assert created_flags == [False, True]

    # Exactly one job exists, exactly one attempt's worth of state.
    rec = await store.get_job("race-job")
    assert rec is not None
    assert rec.attempt_number == 1


async def test_mark_duplicate_increments_counter_and_timestamp():
    store = MemoryJobStore()
    await store.create_job(AiJobRecord(job_id="j11"))
    rec = await store.mark_duplicate("j11")
    assert rec.duplicate_request_count == 1
    assert rec.last_duplicate_request_at is not None
    rec2 = await store.mark_duplicate("j11")
    assert rec2.duplicate_request_count == 2
    assert await store.mark_duplicate("unknown-job") is None


async def test_try_requeue_for_retry_requires_matching_status_and_fingerprint():
    store = MemoryJobStore()
    await store.create_job(
        AiJobRecord(job_id="j12", status=JobStatus.FAILED, request_fingerprint="fp-a", attempt_number=1)
    )

    # Wrong fingerprint -> refused.
    assert await store.try_requeue_for_retry(
        "j12", allowed_statuses=RETRYABLE_JOB_STATUSES, fingerprint="fp-WRONG",
        options=JobOptions(), callback_url=None,
    ) is None

    # Correct fingerprint, retryable status -> succeeds, new attempt, reset lifecycle.
    updated = await store.try_requeue_for_retry(
        "j12", allowed_statuses=RETRYABLE_JOB_STATUSES, fingerprint="fp-a",
        options=JobOptions(), callback_url=None,
    )
    assert updated.status == JobStatus.QUEUED
    assert updated.attempt_number == 2
    assert updated.completed_at is None and updated.failed_at is None

    # Now QUEUED -> no longer retryable; a second retry attempt is refused.
    assert await store.try_requeue_for_retry(
        "j12", allowed_statuses=RETRYABLE_JOB_STATUSES, fingerprint="fp-a",
        options=JobOptions(), callback_url=None,
    ) is None


async def test_try_requeue_for_retry_concurrent_only_one_wins():
    """Two concurrent retries against the same FAILED job: only one transitions
    it to QUEUED with attempt_number+1; the other observes the now-non-
    retryable status and safely no-ops (no double dispatch)."""
    store = MemoryJobStore()
    await store.create_job(
        AiJobRecord(job_id="j13", status=JobStatus.FAILED, request_fingerprint="fp-r", attempt_number=1)
    )

    async def attempt():
        return await store.try_requeue_for_retry(
            "j13", allowed_statuses=RETRYABLE_JOB_STATUSES, fingerprint="fp-r",
            options=JobOptions(), callback_url=None,
        )

    results = await asyncio.gather(attempt(), attempt())
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
    assert winners[0].attempt_number == 2
