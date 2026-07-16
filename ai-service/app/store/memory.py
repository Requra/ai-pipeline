"""
In-memory store implementations.

The default backend when no ``DATABASE_URL`` is configured: dev, demo, and the
test-suite run entirely against these with zero infrastructure. Not durable
across process restarts and not shared across processes — production must use
the PostgreSQL-backed stores in ``app.store.db``.

State is module-level so a single process shares one view across the API and the
in-process queue. A lock guards the dicts because FastAPI may run sync work in a
threadpool.
"""

from __future__ import annotations

import math
import threading
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

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
    TERMINAL_JOB_STATUSES,
    utcnow,
)

_LOCK = threading.RLock()


class MemoryJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, AiJobRecord] = {}
        self._events: Dict[str, List[JobEventRecord]] = {}
        self._attempts: Dict[str, List[JobAttemptRecord]] = {}

    async def create_job(self, record: AiJobRecord) -> AiJobRecord:
        with _LOCK:
            record.updated_at = utcnow()
            if record.queued_at is None:
                record.queued_at = record.created_at
            self._jobs[record.job_id] = record
            return record.model_copy(deep=True)

    async def create_or_get(self, record: AiJobRecord) -> Tuple[AiJobRecord, bool]:
        # Existence-check + insert happen under one lock acquisition with no
        # `await` in between, so this critical section can never be interleaved
        # by another coroutine on the same event loop — the classic "two
        # concurrent requests for the same new job_id" race cannot create two rows.
        with _LOCK:
            existing = self._jobs.get(record.job_id)
            if existing is not None:
                return existing.model_copy(deep=True), False
            record.updated_at = utcnow()
            if record.queued_at is None:
                record.queued_at = record.created_at
            self._jobs[record.job_id] = record
            return record.model_copy(deep=True), True

    async def mark_duplicate(self, job_id: str) -> Optional[AiJobRecord]:
        with _LOCK:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            rec.duplicate_request_count += 1
            rec.last_duplicate_request_at = utcnow()
            return rec.model_copy(deep=True)

    async def try_requeue_for_retry(
        self,
        job_id: str,
        *,
        allowed_statuses: FrozenSet[JobStatus],
        fingerprint: str,
        options: JobOptions,
        callback_url: Optional[str],
    ) -> Optional[AiJobRecord]:
        with _LOCK:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            # Atomic check-and-set: status + fingerprint validated and mutated
            # in the same critical section, so two concurrent retries for the
            # same job_id can never both observe a retryable status and both win.
            if rec.status not in allowed_statuses:
                return None
            if rec.request_fingerprint != fingerprint:
                return None
            now = utcnow()
            rec.attempt_number += 1
            rec.status = JobStatus.QUEUED
            rec.current_node = "queued"
            rec.progress_pct = 0
            rec.cancel_requested = False
            rec.error_code = None
            rec.error_message = None
            rec.options = options
            rec.callback_url = callback_url or options.callback_url
            rec.queued_at = now
            # Clear prior-attempt lifecycle timestamps so /status's derived
            # completed_at doesn't misreport a stale completion while the job
            # is actively re-queued/processing again.
            rec.started_at = None
            rec.completed_at = None
            rec.failed_at = None
            rec.cancelled_at = None
            rec.updated_at = now
            return rec.model_copy(deep=True)

    async def get_job(self, job_id: str) -> Optional[AiJobRecord]:
        with _LOCK:
            rec = self._jobs.get(job_id)
            return rec.model_copy(deep=True) if rec else None

    async def update_job(self, job_id: str, **fields: Any) -> Optional[AiJobRecord]:
        with _LOCK:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            for key, value in fields.items():
                if hasattr(rec, key):
                    setattr(rec, key, value)
            rec.updated_at = utcnow()
            return rec.model_copy(deep=True)

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
        with _LOCK:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            rec.status = status
            now = utcnow()
            if current_node is not None:
                rec.current_node = current_node
            if progress_pct is not None:
                rec.progress_pct = progress_pct
            if error_code is not None:
                rec.error_code = error_code
            if error_message is not None:
                rec.error_message = error_message
            if status == JobStatus.PROCESSING and rec.started_at is None:
                rec.started_at = now
            if status == JobStatus.FAILED:
                rec.failed_at = now
            elif status == JobStatus.CANCELLED:
                rec.cancelled_at = now
            elif status in TERMINAL_JOB_STATUSES:
                rec.completed_at = now
            rec.updated_at = now
            return rec.model_copy(deep=True)

    async def request_cancel(self, job_id: str) -> Optional[AiJobRecord]:
        with _LOCK:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            rec.cancel_requested = True
            rec.updated_at = utcnow()
            return rec.model_copy(deep=True)

    async def is_cancel_requested(self, job_id: str) -> bool:
        with _LOCK:
            rec = self._jobs.get(job_id)
            return bool(rec and rec.cancel_requested)

    async def add_event(self, event: JobEventRecord) -> None:
        with _LOCK:
            self._events.setdefault(event.job_id, []).append(event.model_copy(deep=True))

    async def list_events(self, job_id: str) -> List[JobEventRecord]:
        with _LOCK:
            return [e.model_copy(deep=True) for e in self._events.get(job_id, [])]

    async def add_attempt(self, attempt: JobAttemptRecord) -> None:
        with _LOCK:
            attempts = self._attempts.setdefault(attempt.job_id, [])
            for idx, existing in enumerate(attempts):
                if existing.attempt_number == attempt.attempt_number:
                    update = attempt.model_copy(deep=True)
                    if update.started_at is None:
                        update.started_at = existing.started_at
                    attempts[idx] = update
                    break
            else:
                attempts.append(attempt.model_copy(deep=True))

    async def list_attempts(self, job_id: str) -> List[JobAttemptRecord]:
        with _LOCK:
            return [a.model_copy(deep=True) for a in self._attempts.get(job_id, [])]

    async def cleanup_expired(self, ttl_seconds: int) -> int:
        if ttl_seconds <= 0:
            return 0
        cutoff = utcnow().timestamp() - ttl_seconds
        pruned = 0
        with _LOCK:
            for job_id in list(self._jobs.keys()):
                rec = self._jobs[job_id]
                if rec.status not in TERMINAL_JOB_STATUSES:
                    continue
                terminal = rec.completed_at or rec.failed_at or rec.cancelled_at or rec.updated_at
                if terminal and terminal.timestamp() < cutoff:
                    self._jobs.pop(job_id, None)
                    self._events.pop(job_id, None)
                    self._attempts.pop(job_id, None)
                    pruned += 1
        return pruned


class MemoryResultStore:
    def __init__(self) -> None:
        self._results: Dict[str, JobResultRecord] = {}

    async def save_result(
        self,
        job_id: str,
        result: Dict[str, Any],
        *,
        contract_version: str = "1.0",
        status: str = "completed",
        processing_time_ms: int = 0,
    ) -> JobResultRecord:
        record = JobResultRecord(
            job_id=job_id,
            contract_version=contract_version,
            status=status,
            result_json=result,
            exports_json=result.get("exports", {}) if isinstance(result, dict) else {},
            artifacts_json=result.get("artifacts", {}) if isinstance(result, dict) else {},
            processing_time_ms=processing_time_ms,
        )
        with _LOCK:
            self._results[job_id] = record
        return record.model_copy(deep=True)

    async def get_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        with _LOCK:
            rec = self._results.get(job_id)
            return dict(rec.result_json) if rec else None

    async def get_result_record(self, job_id: str) -> Optional[JobResultRecord]:
        with _LOCK:
            rec = self._results.get(job_id)
            return rec.model_copy(deep=True) if rec else None


class MemoryChunkStore:
    def __init__(self) -> None:
        self._docs: Dict[str, List[SourceDocumentRecord]] = {}
        self._chunks: List[SourceChunkRecord] = []

    async def get_documents(self, job_id: str) -> List[SourceDocumentRecord]:
        with _LOCK:
            return [doc.model_copy(deep=True) for doc in self._docs.get(job_id, [])]

    async def get_document_by_backend_id(self, backend_id: str) -> Optional[SourceDocumentRecord]:
        with _LOCK:
            for job_id, docs in self._docs.items():
                for doc in docs:
                    if doc.backend_document_id == backend_id:
                        return doc.model_copy(deep=True)
            return None

    async def save_documents(
        self, documents: List[SourceDocumentRecord]
    ) -> List[SourceDocumentRecord]:
        saved: List[SourceDocumentRecord] = []
        with _LOCK:
            for job_id in {doc.job_id for doc in documents}:
                self._docs[job_id] = []
            for idx, doc in enumerate(documents):
                if not doc.id:
                    doc = doc.model_copy(update={"id": f"{doc.job_id}:doc:{idx}"})
                self._docs[doc.job_id].append(doc)
                saved.append(doc.model_copy(deep=True))
        return saved

    async def save_chunks(self, chunks: List[SourceChunkRecord]) -> None:
        with _LOCK:
            job_ids = {c.job_id for c in chunks}
            self._chunks = [c for c in self._chunks if c.job_id not in job_ids]
            self._chunks.extend(c.model_copy(deep=True) for c in chunks)

    async def get_chunks(self, job_id: str) -> List[SourceChunkRecord]:
        with _LOCK:
            return [c.model_copy(deep=True) for c in self._chunks if c.job_id == job_id]

    async def find_chunks(
        self,
        *,
        tenant_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        source_document_ids: Optional[List[str]] = None,
        chunk_ids: Optional[List[str]] = None,
    ) -> List[SourceChunkRecord]:
        doc_id_set = set(source_document_ids) if source_document_ids else None
        chunk_id_set = set(chunk_ids) if chunk_ids else None
        with _LOCK:
            out = []
            for c in self._chunks:
                if tenant_id is not None and c.tenant_id != tenant_id:
                    continue
                if project_id is not None and c.project_id != project_id:
                    continue
                if job_id is not None and c.job_id != job_id:
                    continue
                if doc_id_set is not None and c.source_document_id not in doc_id_set:
                    continue
                if chunk_id_set is not None and c.chunk_id not in chunk_id_set:
                    continue
                out.append(c.model_copy(deep=True))
            return out


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class MemoryEmbeddingStore:
    def __init__(self) -> None:
        self._embeddings: List[ChunkEmbeddingRecord] = []

    async def save_embeddings(self, embeddings: List[ChunkEmbeddingRecord]) -> None:
        with _LOCK:
            job_ids = {e.job_id for e in embeddings}
            self._embeddings = [
                e for e in self._embeddings if e.job_id not in job_ids
            ]
            self._embeddings.extend(e.model_copy(deep=True) for e in embeddings)

    async def count_for_job(self, job_id: str) -> int:
        with _LOCK:
            return sum(1 for e in self._embeddings if e.job_id == job_id)

    async def vector_search(
        self,
        query_embedding: List[float],
        *,
        tenant_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if job_id is None and (tenant_id is None or project_id is None):
            raise ValueError(
                "vector_search requires job_id or both tenant_id and project_id"
            )
        with _LOCK:
            scored: List[Dict[str, Any]] = []
            for e in self._embeddings:
                # Tenant/project scoping mirrors the SQL WHERE clause so the
                # in-memory path exercises the same isolation guarantees.
                if tenant_id is not None and e.tenant_id != tenant_id:
                    continue
                if project_id is not None and e.project_id != project_id:
                    continue
                if job_id is not None and e.job_id != job_id:
                    continue
                score = _cosine(query_embedding, e.embedding)
                scored.append(
                    {
                        "chunk_id": e.chunk_id,
                        "job_id": e.job_id,
                        "score": round(score, 6),
                    }
                )
        scored.sort(key=lambda h: h["score"], reverse=True)
        return scored[: max(0, top_k)]
