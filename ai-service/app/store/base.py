"""
Store interfaces (the seams the rest of the service depends on).

Four small, focused, async protocols — each owns one persistence concern:

  * :class:`JobStore`      — ai_jobs lifecycle + events + attempts + cancellation.
  * :class:`ResultStore`   — final JobResult (+ decomposed requirement/story/quality rows).
  * :class:`ChunkStore`    — source documents + source chunks (persistent RAG corpus).
  * :class:`EmbeddingStore`— pgvector-backed chunk embeddings + vector search.

All methods are ``async`` so a real asyncpg/SQLAlchemy backend fits without
changing callers; the in-memory implementations satisfy the same contract
synchronously. Concrete implementations live in ``app.store.memory`` (default,
dev/tests) and ``app.store.db`` (PostgreSQL + pgvector, production).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Protocol, Tuple, runtime_checkable

from app.store.models import (
    AiJobRecord,
    ChunkEmbeddingRecord,
    JobAttemptRecord,
    JobEventRecord,
    JobOptions,
    JobResultRecord,
    JobStatus,
    SourceChunkRecord,
    SourceDocumentRecord,
)


@runtime_checkable
class JobStore(Protocol):
    async def create_job(self, record: AiJobRecord) -> AiJobRecord: ...

    async def create_or_get(self, record: AiJobRecord) -> Tuple[AiJobRecord, bool]:
        """Atomic get-or-create by ``record.job_id``.

        Returns ``(row, created)``. When two callers race to create the same
        ``job_id`` concurrently, exactly one observes ``created=True`` (and
        proceeds to enqueue); the other observes ``created=False`` with the
        winner's row (and must not enqueue again). Implementations must make
        the existence-check and the insert a single atomic operation — a
        separate ``get_job`` followed by ``create_job`` is NOT race-safe.
        """
        ...

    async def mark_duplicate(self, job_id: str) -> Optional[AiJobRecord]:
        """Record that a duplicate request for an existing job_id arrived.

        Increments ``duplicate_request_count`` and stamps
        ``last_duplicate_request_at``. Called for every repeat submission
        regardless of whether the payload matches (idempotent hit or conflict).
        """
        ...

    async def try_requeue_for_retry(
        self,
        job_id: str,
        *,
        allowed_statuses: FrozenSet[JobStatus],
        fingerprint: str,
        options: JobOptions,
        callback_url: Optional[str],
    ) -> Optional[AiJobRecord]:
        """Atomically retry a job: bump attempt_number and reset to QUEUED.

        Only transitions when, in one atomic check-and-set, the job's *current*
        status is in ``allowed_statuses`` AND its stored ``request_fingerprint``
        equals ``fingerprint``. Returns the updated row on success, or ``None``
        if the precondition did not hold (status changed, or fingerprint
        mismatch) — including when a concurrent retry already won the race.
        Implementations must hold this check-and-set atomically (e.g. a single
        transaction with ``SELECT ... FOR UPDATE`` in Postgres, or the process
        lock in the in-memory store) so two concurrent retries can never both
        succeed.
        """
        ...

    async def get_job(self, job_id: str) -> Optional[AiJobRecord]: ...

    async def update_job(self, job_id: str, **fields: Any) -> Optional[AiJobRecord]:
        """Patch arbitrary AiJobRecord fields; bumps updated_at."""
        ...

    async def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        current_node: Optional[str] = None,
        progress_pct: Optional[int] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[AiJobRecord]:
        """Transition status and stamp the matching lifecycle timestamp."""
        ...

    async def request_cancel(self, job_id: str) -> Optional[AiJobRecord]: ...

    async def is_cancel_requested(self, job_id: str) -> bool: ...

    async def add_event(self, event: JobEventRecord) -> None: ...

    async def list_events(self, job_id: str) -> List[JobEventRecord]: ...

    async def add_attempt(self, attempt: JobAttemptRecord) -> None: ...

    async def list_attempts(self, job_id: str) -> List[JobAttemptRecord]: ...

    async def cleanup_expired(self, ttl_seconds: int) -> int: ...


@runtime_checkable
class ResultStore(Protocol):
    async def save_result(
        self,
        job_id: str,
        result: Dict[str, Any],
        *,
        contract_version: str = "1.0",
        status: str = "completed",
        processing_time_ms: int = 0,
    ) -> JobResultRecord: ...

    async def get_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the persisted JobResult JSON (contract payload) or None."""
        ...

    async def get_result_record(self, job_id: str) -> Optional[JobResultRecord]: ...


@runtime_checkable
class ChunkStore(Protocol):
    async def save_documents(
        self, documents: List[SourceDocumentRecord]
    ) -> List[SourceDocumentRecord]: ...

    async def save_chunks(self, chunks: List[SourceChunkRecord]) -> None: ...

    async def get_chunks(self, job_id: str) -> List[SourceChunkRecord]: ...

    async def find_chunks(
        self,
        *,
        tenant_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        source_document_ids: Optional[List[str]] = None,
        chunk_ids: Optional[List[str]] = None,
    ) -> List[SourceChunkRecord]:
        """Filtered lookup used by retrieval — always tenant/project scoped."""
        ...


@runtime_checkable
class EmbeddingStore(Protocol):
    async def save_embeddings(self, embeddings: List[ChunkEmbeddingRecord]) -> None: ...

    async def count_for_job(self, job_id: str) -> int: ...

    async def vector_search(
        self,
        query_embedding: List[float],
        *,
        tenant_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return ranked ``{chunk_id, score, ...}`` hits, tenant/project scoped.

        The filters are mandatory-by-convention for production: a caller MUST
        pass tenant_id + project_id (or job_id) so semantic recall never leaks
        across tenants/projects.
        """
        ...


@dataclass
class StoreBundle:
    """Grouping of the four stores handed to the worker/runner and API layer."""

    jobs: JobStore
    results: ResultStore
    chunks: ChunkStore
    embeddings: EmbeddingStore
