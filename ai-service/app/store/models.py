"""
Storage-agnostic domain records exchanged by the store interfaces.

These Pydantic models are the seam between the pipeline/API layers and whatever
backend actually persists them (in-memory for dev/tests, PostgreSQL + pgvector
in production). They deliberately do *not* import SQLAlchemy so the default
in-memory path works with zero infra and the test suite runs without a database.

Timestamps are timezone-aware UTC ``datetime``. The public ``/status`` contract
historically returns float epoch seconds, so :meth:`AiJobRecord.to_status_view`
converts them back to floats to stay byte-for-byte compatible with existing
clients and tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_epoch(dt: Optional[datetime]) -> Optional[float]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class JobStatus(str, Enum):
    """Durable lifecycle statuses for an AI job.

    Note: the *public* ``/status`` contract has always surfaced the uppercase
    string values directly, so the enum values match that vocabulary. The final
    ``JobResult.status`` (lowercase: completed/partial/failed/rejected) is a
    separate, contract-level field produced by the ``format`` node.
    """

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


#: Statuses after which no further processing happens for the current attempt.
TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.PARTIAL,
        JobStatus.REJECTED,
    }
)

#: "Currently being worked on" statuses for idempotency purposes. Our concrete
#: status vocabulary only has QUEUED/PROCESSING as non-terminal states (there is
#: no separate STARTED/RETRYING status — a retry re-enters QUEUED under the same
#: job_id with a bumped attempt_number), so both map onto "active/running" here.
ACTIVE_JOB_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.PROCESSING})

#: Statuses a retry (POST /internal/jobs with reprocess=true, or POST
#: .../retry) may transition out of. Retrying a QUEUED/PROCESSING/COMPLETED/
#: PARTIAL/REJECTED job is refused — only FAILED/CANCELLED jobs are retryable.
RETRYABLE_JOB_STATUSES = frozenset({JobStatus.FAILED, JobStatus.CANCELLED})


class InputType(str, Enum):
    TEXT = "text"
    BACKEND_DOCUMENT = "backend_document"
    BACKEND_TRANSCRIPT = "backend_transcript"
    BACKEND_AUDIO = "backend_audio"
    BACKEND_SOURCES = "backend_sources"


class SourceDocumentRef(BaseModel):
    """A backend-owned document reference (AI service never owns the raw file)."""

    document_id: str
    file_type: Optional[str] = None
    mime_type: Optional[str] = None
    storage_key: Optional[str] = None
    file_url: Optional[str] = None
    sha256_hash: Optional[str] = None
    page_count: Optional[int] = None
    file_name: Optional[str] = None
    language: Optional[str] = None


class JobOptions(BaseModel):
    """Per-job processing options (persisted verbatim in ai_jobs.options_json)."""

    generate_user_stories: bool = True
    generate_summary: bool = True
    enable_embeddings: bool = False
    enable_hybrid_retrieval: bool = False
    language: str = "en"
    callback_url: Optional[str] = None
    priority: str = "normal"


class AiJobRecord(BaseModel):
    job_id: str
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None
    requested_by: Optional[str] = None
    input_type: str = InputType.TEXT.value
    status: JobStatus = JobStatus.QUEUED
    current_node: str = "queued"
    progress_pct: int = 0
    attempt_number: int = 1
    options: JobOptions = Field(default_factory=JobOptions)
    contract_version: str = "1.0"
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    callback_url: Optional[str] = None
    # Cancellation is cooperative: the API sets this flag, the worker observes it
    # between pipeline nodes and stops safely.
    cancel_requested: bool = False

    # --- Idempotency / duplicate-request tracking -------------------------
    # Deterministic hash of the production-relevant request fields (see
    # app.services.fingerprint). Used to distinguish a true duplicate
    # submission (same fingerprint) from a job_id reused for a different
    # request (different fingerprint -> 409 conflict).
    request_fingerprint: Optional[str] = None
    request_fingerprint_version: Optional[int] = None
    # Optional caller-supplied idempotency key (distinct from job_id), reserved
    # for callers that want to key idempotency on something other than job_id.
    idempotency_key: Optional[str] = None
    last_duplicate_request_at: Optional[datetime] = None
    duplicate_request_count: int = 0

    created_at: datetime = Field(default_factory=utcnow)
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)

    def to_status_view(self, result: Any = None) -> Dict[str, Any]:
        """Project to the stable public ``/status`` shape (float epoch times)."""
        # Map durable statuses onto the historical public status vocabulary.
        # PARTIAL/REJECTED are terminal-with-caveats; the public contract has
        # always used COMPLETED for those (the nuance lives in result.status).
        public_status = self.status.value
        if self.status in (JobStatus.PARTIAL, JobStatus.REJECTED):
            public_status = "COMPLETED"

        error_view: Any = None
        if self.error_message:
            error_view = (
                f"{self.error_code}: {self.error_message}"
                if self.error_code
                else self.error_message
            )

        terminal_time = (
            self.completed_at or self.failed_at or self.cancelled_at
        )
        return {
            "job_id": self.job_id,
            "status": public_status,
            "progress_pct": int(self.progress_pct or 0),
            "current_node": self.current_node,
            "result": result,
            "error": error_view,
            "created_at": _to_epoch(self.created_at),
            "updated_at": _to_epoch(self.updated_at),
            "completed_at": _to_epoch(terminal_time),
        }

    def to_internal_view(
        self, *, warning_count: int = 0, quality_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """Richer internal status view for ``/internal/jobs/{job_id}``."""
        base = self.to_status_view()
        base.pop("result", None)
        base.update(
            {
                "status": self.status.value,  # exact durable status, no mapping
                "attempt_number": self.attempt_number,
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "input_type": self.input_type,
                "error_code": self.error_code,
                "warning_count": warning_count,
                "quality_score": quality_score,
                "links": {
                    "result": f"/internal/jobs/{self.job_id}/result",
                    "cancel": f"/internal/jobs/{self.job_id}/cancel",
                    "retry": f"/internal/jobs/{self.job_id}/retry",
                },
            }
        )
        return base


class JobEventRecord(BaseModel):
    job_id: str
    event_type: str
    node_name: Optional[str] = None
    message: str = ""
    severity: str = "info"  # info | warning | error
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class JobAttemptRecord(BaseModel):
    job_id: str
    attempt_number: int
    status: JobStatus = JobStatus.PROCESSING
    started_at: Optional[datetime] = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class SourceDocumentRecord(BaseModel):
    job_id: str
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None
    backend_document_id: Optional[str] = None
    source_type: str = "text"
    file_name: str = "unknown"
    mime_type: str = "application/octet-stream"
    storage_key: Optional[str] = None
    file_url: Optional[str] = None
    sha256_hash: Optional[str] = None
    language: str = "en"
    page_count: Optional[int] = None
    # Set by the store on save so chunks can reference their document.
    id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class SourceChunkRecord(BaseModel):
    job_id: str
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None
    source_document_id: Optional[str] = None
    chunk_id: str
    chunk_index: int = 0
    text: str = ""
    page_number: Optional[int] = None
    speaker: Optional[str] = None
    start_time_sec: Optional[float] = None
    end_time_sec: Optional[float] = None
    start_char: int = 0
    end_char: int = 0
    token_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class ChunkEmbeddingRecord(BaseModel):
    chunk_id: str
    job_id: str
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None
    embedding_model: str
    embedding: List[float]
    created_at: datetime = Field(default_factory=utcnow)


class JobResultRecord(BaseModel):
    job_id: str
    contract_version: str = "1.0"
    status: str = "completed"
    result_json: Dict[str, Any] = Field(default_factory=dict)
    exports_json: Dict[str, Any] = Field(default_factory=dict)
    artifacts_json: Dict[str, Any] = Field(default_factory=dict)
    processing_time_ms: int = 0
    created_at: datetime = Field(default_factory=utcnow)
