"""
Lightweight in-memory progress store for the demo / direct-mode pipeline.

Public shape returned via `/status/{job_id}` stays backward compatible:

    {
        "job_id":       str,
        "status":       "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED",
        "progress_pct": int,
        "current_node": str,
        "result":       Any | None,
        "error":        Any | None,
        # Optional timestamp fields below were added for the harden/
        # direct-demo-contract change. Existing clients ignore them.
        "created_at":   float,
        "updated_at":   float,
        "completed_at": float | None,
    }

There is no database here on purpose — the direct-mode demo is short-lived
and adding a real store is out of scope. We still:

  * Track created/updated/completed timestamps so debugging is sane.
  * Expose `cleanup_expired_jobs(ttl_seconds)` so a caller can prune stale
    entries before they leak memory in a long-running dev server.

`cleanup_expired_jobs` is intentionally NOT wired into a background task to
keep behavior predictable — call it from a startup hook or a scheduled
endpoint when you need it.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable

# In-memory database of running job processes
progress_store: Dict[str, Dict[str, Any]] = {}

# Statuses we consider terminal — completed_at gets set on transition.
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED"})

# Canonical node-to-progress percentage mapping for the active LangGraph pipeline
PROGRESS_BY_NODE: Dict[str, int] = {
    "detect_file_type": 5,
    "prepare_sources": 20,
    "build_source_index": 35,
    "extract": 45,
    "dedupe_requirements": 55,
    "retrieve_evidence": 62,
    "classify": 70,
    "evidence_grounding": 76,
    "generate": 85,
    "quality_gate": 90,
    "repair_stories": 92,
    "summarize": 95,
    "format": 100,
}


def update_progress(
    job_id: str,
    node_name: str,
    progress_pct: int,
    status: str = "PROCESSING",
    result: Any = None,
    error: Any = None,
) -> None:
    """Create or update a job's progress record (thread-naive — single asyncio loop)."""
    if not job_id:
        return

    now = time.time()

    if job_id not in progress_store:
        progress_store[job_id] = {
            "job_id": job_id,
            "status": "QUEUED",
            "progress_pct": 0,
            "current_node": "started",
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }

    entry = progress_store[job_id]
    entry["job_id"] = job_id
    entry["status"] = status
    entry["progress_pct"] = progress_pct
    entry["current_node"] = node_name
    entry["updated_at"] = now

    if result is not None:
        entry["result"] = result
    if error is not None:
        entry["error"] = error

    if status in TERMINAL_STATUSES and entry.get("completed_at") is None:
        entry["completed_at"] = now


def get_status(job_id: str) -> Dict[str, Any] | None:
    """Return the public status shape, or None when unknown."""
    return progress_store.get(job_id)


def cleanup_expired_jobs(ttl_seconds: int = 60 * 60) -> int:
    """
    Drop entries whose final timestamp is older than `ttl_seconds`.

    Active (non-terminal) jobs age out from `updated_at`; terminal jobs from
    `completed_at`. Returns the count of pruned entries.
    """
    if ttl_seconds <= 0:
        return 0

    cutoff = time.time() - ttl_seconds
    stale: Iterable[str] = [
        job_id
        for job_id, entry in progress_store.items()
        if _last_touched(entry) < cutoff
    ]
    pruned = 0
    for job_id in stale:
        progress_store.pop(job_id, None)
        pruned += 1
    return pruned


def _last_touched(entry: Dict[str, Any]) -> float:
    """Most recent activity time for an entry — completed_at on terminal jobs."""
    completed = entry.get("completed_at")
    if completed is not None:
        return float(completed)
    return float(entry.get("updated_at", entry.get("created_at", 0.0)))
