"""
Job store abstraction for the async pipeline.

This is the seam other layers should depend on instead of poking the raw
``app.progress.progress_store`` dict directly. For the MVP/demo the only
implementation is :class:`MemoryJobStore`, which is backed by the *same*
``app.progress.progress_store`` dict so that:

  * Nodes that call the legacy ``app.progress.update_progress(...)`` helper, and
  * Callers that use this store,

share a single source of truth. Swapping in a Redis/DB-backed store later only
requires implementing the :class:`JobStore` protocol.

No secrets, no raw document text, and no full LLM payloads are stored here — only
the public status shape returned by ``GET /status/{job_id}``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from app import progress as _progress

# Public, stable status vocabulary surfaced via /status.
VALID_JOB_STATUSES = ("QUEUED", "PROCESSING", "COMPLETED", "FAILED")

# Caller-supplied job ids must be filesystem/URL/log safe and bounded.
JOB_ID_MAX_LEN = 128
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def sanitize_job_id(job_id: Any) -> str:
    """Validate a caller-supplied job id.

    Accepts only non-empty strings of letters, digits, ``.``, ``_`` and ``-``
    up to :data:`JOB_ID_MAX_LEN` characters. Returns the trimmed id.

    Raises:
        ValueError: when the id is missing, empty, too long, or contains unsafe
            characters. Callers map this to an HTTP 400.
    """
    if not isinstance(job_id, str):
        raise ValueError("job_id must be a string")
    candidate = job_id.strip()
    if not candidate:
        raise ValueError("job_id must not be empty")
    if len(candidate) > JOB_ID_MAX_LEN:
        raise ValueError(f"job_id must be at most {JOB_ID_MAX_LEN} characters")
    if not _JOB_ID_RE.match(candidate):
        raise ValueError(
            "job_id may only contain letters, digits, '.', '_' and '-'"
        )
    return candidate


class JobStatus(BaseModel):
    """Typed view of the public ``/status`` shape.

    Field set and types mirror the dict stored in ``progress_store`` exactly so
    serialization stays backward compatible.
    """

    job_id: str
    status: str = "QUEUED"
    progress_pct: int = 0
    current_node: str = "started"
    result: Optional[Any] = None
    error: Optional[Any] = None
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    completed_at: Optional[float] = None


@runtime_checkable
class JobStore(Protocol):
    """Minimal contract for a job lifecycle store."""

    def create(self, job_id: str, *, node_name: str = "started", status: str = "QUEUED") -> None: ...

    def update(
        self,
        job_id: str,
        node_name: str,
        progress_pct: int,
        status: str = "PROCESSING",
        result: Any = None,
        error: Any = None,
    ) -> None: ...

    def get(self, job_id: str) -> Optional[Dict[str, Any]]: ...

    def get_status(self, job_id: str) -> Optional[JobStatus]: ...

    def cleanup_expired(self, ttl_seconds: int) -> int: ...


class MemoryJobStore:
    """In-memory :class:`JobStore` backed by ``app.progress.progress_store``.

    Reuses ``app.progress`` helpers so legacy node calls to ``update_progress``
    and store calls stay consistent. Not durable across process restarts —
    acceptable for the direct-mode demo; documented as a known limitation.
    """

    def __init__(self, store: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        # Default to the shared module-level store (single source of truth).
        self._store: Dict[str, Dict[str, Any]] = (
            store if store is not None else _progress.progress_store
        )

    def create(self, job_id: str, *, node_name: str = "started", status: str = "QUEUED") -> None:
        _progress.update_progress(
            job_id=job_id, node_name=node_name, progress_pct=0, status=status
        )

    def update(
        self,
        job_id: str,
        node_name: str,
        progress_pct: int,
        status: str = "PROCESSING",
        result: Any = None,
        error: Any = None,
    ) -> None:
        _progress.update_progress(
            job_id=job_id,
            node_name=node_name,
            progress_pct=progress_pct,
            status=status,
            result=result,
            error=error,
        )

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the raw status dict (public shape) or None when unknown."""
        return self._store.get(job_id)

    def get_status(self, job_id: str) -> Optional[JobStatus]:
        """Return a typed :class:`JobStatus` or None when unknown."""
        entry = self._store.get(job_id)
        if entry is None:
            return None
        return JobStatus(**{key: entry.get(key) for key in JobStatus.model_fields})

    def cleanup_expired(self, ttl_seconds: int = 60 * 60) -> int:
        return _progress.cleanup_expired_jobs(ttl_seconds)


# Default process-wide store used by the API layer.
default_job_store: JobStore = MemoryJobStore()
