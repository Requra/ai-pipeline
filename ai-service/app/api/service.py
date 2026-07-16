"""
Shared job-creation + status service.

Every entry point — the demo ``/process`` and ``/process-json`` endpoints and the
production ``/internal/jobs`` endpoint — funnels through here so they all use the
same DB-backed job record + queue dispatch. This is what makes the demo paths
"the same system" internally while preserving their public contracts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.progress import progress_store, update_progress
from app.services.fingerprint import FINGERPRINT_VERSION, compute_job_request_fingerprint
from app.store.factory import get_stores
from app.store.models import (
    ACTIVE_JOB_STATUSES,
    AiJobRecord,
    JobEventRecord,
    JobOptions,
    JobStatus,
    RETRYABLE_JOB_STATUSES,
)
from app.worker.dispatch import dispatch_job
from app.worker.state import make_initial_state

logger = logging.getLogger("app.api.service")


def resolve_pipeline() -> Any:
    """Return the process pipeline (late-bound to ``app.main.pipeline``).

    Late binding keeps a single mock-patch point (``patch('app.main.pipeline')``)
    working uniformly for demo and internal endpoints in tests.
    """
    import app.main as main_module

    return main_module.pipeline


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------

async def get_or_create_job(
    *,
    job_id: str,
    input_type: str,
    tenant_id: Optional[str] = None,
    project_id: Optional[str] = None,
    requested_by: Optional[str] = None,
    options: Optional[JobOptions] = None,
    callback_url: Optional[str] = None,
    reprocess: bool = False,
) -> Tuple[AiJobRecord, bool, bool]:
    """Idempotent job creation.

    Returns ``(record, created, is_retry)``:
      * existing job + not reprocess → ``(existing, False, False)`` (idempotent).
      * existing job + reprocess     → new attempt, ``(record, True, True)``.
      * no existing job              → created, ``(record, True, False)``.
    """
    stores = get_stores()
    options = options or JobOptions()
    existing = await stores.jobs.get_job(job_id)

    if existing is not None and not reprocess:
        return existing, False, False

    if existing is not None and reprocess:
        attempt = existing.attempt_number + 1
        rec = await stores.jobs.update_job(
            job_id,
            status=JobStatus.QUEUED,
            current_node="queued",
            progress_pct=0,
            attempt_number=attempt,
            cancel_requested=False,
            error_code=None,
            error_message=None,
            options=options,
            callback_url=callback_url or options.callback_url,
        )
        return rec, True, True

    rec = AiJobRecord(
        job_id=job_id,
        tenant_id=tenant_id,
        project_id=project_id,
        requested_by=requested_by,
        input_type=input_type,
        status=JobStatus.QUEUED,
        current_node="queued",
        options=options,
        callback_url=callback_url or options.callback_url,
    )
    await stores.jobs.create_job(rec)
    # Mirror an initial QUEUED entry into the legacy progress store so the public
    # /status endpoint reflects the job immediately even before the worker starts.
    progress_store.setdefault(
        job_id,
        {
            "job_id": job_id,
            "status": "QUEUED",
            "progress_pct": 0,
            "current_node": "queued",
            "result": None,
            "error": None,
            "created_at": rec.created_at.timestamp(),
            "updated_at": rec.updated_at.timestamp(),
            "completed_at": None,
        },
    )
    return rec, True, False


# ---------------------------------------------------------------------------
# Idempotent job creation for POST /internal/jobs
# ---------------------------------------------------------------------------
#
# job_id alone cannot tell whether a repeated request is a safe retry of the
# *same* logical request or an accidental reuse of a job_id for a *different*
# request. `handle_job_creation` decides this by comparing a deterministic
# request fingerprint (see app.services.fingerprint) against the fingerprint
# stored on the existing job, and branches on the existing job's current
# status. See docs/production-architecture.md for the full behavior matrix.

@dataclass
class JobCreationOutcome:
    """Result of `handle_job_creation` — tells the endpoint what to do next."""

    http_status: int
    body: Dict[str, Any]
    # Set only when the caller must build pipeline state and dispatch a job
    # (brand-new job, or an accepted retry). None otherwise (idempotent hit,
    # conflict, or a race lost to a concurrent identical request).
    job: Optional[AiJobRecord] = None
    dispatch: bool = False


def _links(job_id: str) -> Dict[str, str]:
    return {
        "self": f"/internal/jobs/{job_id}",
        "result": f"/internal/jobs/{job_id}/result",
        "cancel": f"/internal/jobs/{job_id}/cancel",
        "retry": f"/internal/jobs/{job_id}/retry",
    }


def _conflict_body(
    job_id: str, message: str, *, existing_status: Optional[str], hint: str, code: str = "JOB_ID_CONFLICT"
) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message, "job_id": job_id, "hint": hint}
    if existing_status is not None:
        error["existing_status"] = existing_status
    return {"error": error}


async def _log_job_event(stores, job_id: str, event_type: str, *, severity: str, metadata: Dict[str, Any]) -> None:
    """Best-effort audit event. Metadata is booleans/status strings only —
    never raw request content, prompts, or secrets."""
    try:
        await stores.jobs.add_event(
            JobEventRecord(job_id=job_id, event_type=event_type, severity=severity, metadata=metadata)
        )
    except Exception:  # pragma: no cover - logging must never break the response
        logger.warning("failed to record job event %s for job_id=%s", event_type, job_id)


def _mirror_progress_store(record: AiJobRecord) -> None:
    """Reflect a create/retry transition into the legacy in-memory /status view."""
    update_progress(record.job_id, record.current_node, record.progress_pct, record.status.value)


async def handle_job_creation(
    req: Any,  # app.api.schemas.CreateJobRequest (typed loosely to avoid an import cycle)
    *,
    job_id: str,
    request_id: Optional[str] = None,
) -> JobCreationOutcome:
    """Idempotent, race-safe job creation/duplicate-handling for POST /internal/jobs.

    Behavior (see docs/production-architecture.md for the full matrix):
      * New job_id                          -> create + dispatch (202).
      * Existing job, different tenant/project (same job_id)
                                             -> 409, no detail leak.
      * Existing job, running, same payload  -> 202 idempotent (no re-enqueue).
      * Existing job, running, diff payload  -> 409.
      * Existing job, completed/partial/rejected, same payload -> 200 idempotent.
      * Existing job, completed/partial/rejected, diff payload -> 409.
      * Existing job, failed/cancelled, same payload, no retry -> 200 (report only).
      * Existing job, failed/cancelled, same payload, reprocess=true -> 202, new attempt.
      * reprocess=true on a non-retryable (running/terminal-success) job -> 409.
      * Any repeated submission on an existing job_id, matched or not, bumps
        duplicate_request_count / last_duplicate_request_at and is recorded via
        a DUPLICATE_REQUEST (and, on conflict, an additional JOB_ID_CONFLICT) event.
    """
    stores = get_stores()
    fingerprint = compute_job_request_fingerprint(req)
    options = JobOptions(
        generate_user_stories=req.options.generate_user_stories,
        generate_summary=req.options.generate_summary,
        enable_embeddings=req.options.enable_embeddings,
        enable_hybrid_retrieval=req.options.enable_hybrid_retrieval,
        language=req.options.language,
        callback_url=req.options.callback_url,
        priority=req.options.priority,
    )

    candidate = AiJobRecord(
        job_id=job_id,
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        requested_by=req.requested_by or req.user_id,
        input_type=req.input_type,
        status=JobStatus.QUEUED,
        current_node="queued",
        options=options,
        callback_url=options.callback_url,
        request_fingerprint=fingerprint,
        request_fingerprint_version=FINGERPRINT_VERSION,
    )

    # Atomic get-or-create: exactly one concurrent caller observes created=True.
    record, created = await stores.jobs.create_or_get(candidate)

    if created:
        _mirror_progress_store(record)
        return JobCreationOutcome(
            http_status=202,
            body={
                "job_id": job_id,
                "status": JobStatus.QUEUED.value,
                "attempt_number": record.attempt_number,
                "idempotent": False,
                "links": _links(job_id),
            },
            job=record,
            dispatch=True,
        )

    existing = record  # a row already existed prior to this call

    # Cross-tenant/-project collision on a (globally unique) job_id: refuse
    # without revealing the other tenant's status, fingerprint, or result.
    if existing.tenant_id != req.tenant_id or existing.project_id != req.project_id:
        await stores.jobs.mark_duplicate(job_id)
        await _log_job_event(
            stores, job_id, "JOB_ID_CONFLICT", severity="warning",
            metadata={"reason": "cross_tenant_or_project", "request_id": request_id},
        )
        return JobCreationOutcome(
            http_status=409,
            body=_conflict_body(
                job_id, "A job with this job_id already exists.",
                existing_status=None, hint="Use a new job_id.",
            ),
        )

    fingerprint_match = existing.request_fingerprint == fingerprint

    # --- Explicit retry flag (`reprocess`) --------------------------------
    if req.reprocess:
        if existing.status not in RETRYABLE_JOB_STATUSES:
            await stores.jobs.mark_duplicate(job_id)
            await _log_job_event(
                stores, job_id, "JOB_ID_CONFLICT", severity="warning",
                metadata={
                    "reason": "retry_not_allowed", "existing_status": existing.status.value,
                    "request_id": request_id,
                },
            )
            return JobCreationOutcome(
                http_status=409,
                body=_conflict_body(
                    job_id, f"Cannot retry a job in status {existing.status.value}.",
                    existing_status=existing.status.value,
                    hint="Retry is only allowed for FAILED or CANCELLED jobs.",
                    code="JOB_NOT_RETRYABLE",
                ),
            )
        if not fingerprint_match:
            await stores.jobs.mark_duplicate(job_id)
            await _log_job_event(
                stores, job_id, "JOB_ID_CONFLICT", severity="warning",
                metadata={
                    "reason": "fingerprint_mismatch_on_retry",
                    "existing_status": existing.status.value, "request_id": request_id,
                },
            )
            return JobCreationOutcome(
                http_status=409,
                body=_conflict_body(
                    job_id,
                    "A job with this job_id already exists with a different request payload.",
                    existing_status=existing.status.value,
                    hint="Use a new job_id, or resubmit retry with the original request payload.",
                ),
            )

        updated = await stores.jobs.try_requeue_for_retry(
            job_id, allowed_statuses=RETRYABLE_JOB_STATUSES, fingerprint=fingerprint,
            options=options, callback_url=options.callback_url,
        )
        if updated is None:
            # Lost the race to a concurrent identical retry — the winner
            # already handles dispatch; report the current state idempotently.
            current = await stores.jobs.get_job(job_id) or existing
            await stores.jobs.mark_duplicate(job_id)
            await _log_job_event(
                stores, job_id, "DUPLICATE_REQUEST", severity="info",
                metadata={
                    "same_fingerprint": True, "existing_status": current.status.value,
                    "request_id": request_id, "reason": "concurrent_retry",
                },
            )
            return JobCreationOutcome(
                http_status=202,
                body={
                    "job_id": job_id, "status": current.status.value,
                    "progress_pct": current.progress_pct, "current_node": current.current_node,
                    "attempt_number": current.attempt_number, "idempotent": True,
                    "duplicate_of": job_id,
                    "message": "A retry for this job is already in progress; duplicate request was not enqueued.",
                },
            )

        await _log_job_event(
            stores, job_id, "DUPLICATE_REQUEST", severity="info",
            metadata={
                "same_fingerprint": True, "existing_status": existing.status.value,
                "request_id": request_id, "retried": True,
            },
        )
        _mirror_progress_store(updated)
        return JobCreationOutcome(
            http_status=202,
            body={
                "job_id": job_id, "status": JobStatus.QUEUED.value,
                "attempt_number": updated.attempt_number, "retried": True,
                "message": "Failed/cancelled job was queued for retry.",
            },
            job=updated,
            dispatch=True,
        )

    # --- No retry flag: pure idempotency / conflict check -----------------
    await stores.jobs.mark_duplicate(job_id)
    await _log_job_event(
        stores, job_id, "DUPLICATE_REQUEST", severity="info",
        metadata={
            "same_fingerprint": fingerprint_match, "existing_status": existing.status.value,
            "request_id": request_id,
        },
    )

    if not fingerprint_match:
        await _log_job_event(
            stores, job_id, "JOB_ID_CONFLICT", severity="warning",
            metadata={
                "existing_status": existing.status.value,
                "fingerprint_version": FINGERPRINT_VERSION, "request_id": request_id,
            },
        )
        return JobCreationOutcome(
            http_status=409,
            body=_conflict_body(
                job_id,
                "A job with this job_id already exists with a different request payload.",
                existing_status=existing.status.value,
                hint="Use a new job_id, or cancel/retry the existing job after it reaches a terminal state.",
            ),
        )

    # Fingerprint matches the existing job -> idempotent, status-dependent response.
    if existing.status in ACTIVE_JOB_STATUSES:
        return JobCreationOutcome(
            http_status=202,
            body={
                "job_id": job_id, "status": existing.status.value,
                "progress_pct": existing.progress_pct, "current_node": existing.current_node,
                "attempt_number": existing.attempt_number, "idempotent": True,
                "duplicate_of": job_id,
                "message": "Job is already running; duplicate request was not enqueued.",
            },
        )

    if existing.status in (JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.REJECTED):
        result = await stores.results.get_result(job_id)
        return JobCreationOutcome(
            http_status=200,
            body={
                "job_id": job_id, "status": existing.status.value, "idempotent": True,
                "duplicate_of": job_id, "result_available": result is not None,
                "links": {"result": f"/internal/jobs/{job_id}/result"},
            },
        )

    if existing.status == JobStatus.FAILED:
        return JobCreationOutcome(
            http_status=200,
            body={
                "job_id": job_id, "status": JobStatus.FAILED.value, "idempotent": True,
                "duplicate_of": job_id,
                "message": (
                    "Job previously failed. Resubmit with reprocess=true (matching payload), "
                    "or POST /internal/jobs/{job_id}/retry, to retry."
                ),
                "links": {"retry": f"/internal/jobs/{job_id}/retry"},
            },
        )

    # existing.status == JobStatus.CANCELLED
    return JobCreationOutcome(
        http_status=200,
        body={
            "job_id": job_id, "status": JobStatus.CANCELLED.value, "idempotent": True,
            "duplicate_of": job_id,
            "message": (
                "Job was previously cancelled. Resubmit with reprocess=true (matching payload), "
                "or POST /internal/jobs/{job_id}/retry, to retry."
            ),
            "links": {"retry": f"/internal/jobs/{job_id}/retry"},
        },
    )


# ---------------------------------------------------------------------------
# Status views
# ---------------------------------------------------------------------------

async def public_status_view(job_id: str) -> Optional[Dict[str, Any]]:
    """Public ``/status`` shape — DB-backed in production, progress store in dev.

    In production (DATABASE_URL set) the durable store is authoritative because
    ``progress_store`` is process-local and not shared across API/worker
    instances. In dev/tests it prefers the in-memory progress store so directly
    injected entries keep working.
    """
    stores = get_stores()

    async def _from_durable() -> Optional[Dict[str, Any]]:
        rec = await stores.jobs.get_job(job_id)
        if rec is None:
            return None
        result = await stores.results.get_result(job_id)
        return rec.to_status_view(result=result)

    if settings.use_database:
        view = await _from_durable()
        if view is not None:
            return view
        entry = progress_store.get(job_id)
        return _entry_view(entry) if entry else None

    entry = progress_store.get(job_id)
    if entry is not None:
        return _entry_view(entry)
    return await _from_durable()


def _entry_view(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": entry.get("job_id"),
        "status": entry.get("status", "QUEUED"),
        "progress_pct": int(entry.get("progress_pct", 0) or 0),
        "current_node": entry.get("current_node", "started"),
        "result": entry.get("result"),
        "error": entry.get("error"),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        "completed_at": entry.get("completed_at"),
    }


async def internal_status_view(job_id: str) -> Optional[Dict[str, Any]]:
    """Richer internal status — always from the durable store."""
    stores = get_stores()
    rec = await stores.jobs.get_job(job_id)
    if rec is None:
        return None
    events = await stores.jobs.list_events(job_id)
    warning_count = sum(1 for e in events if e.severity == "warning")
    quality_score = None
    result = await stores.results.get_result(job_id)
    if isinstance(result, dict):
        report = result.get("quality_report")
        if isinstance(report, dict):
            quality_score = report.get("overall_score")
    return rec.to_internal_view(warning_count=warning_count, quality_score=quality_score)


async def prepare_and_dispatch_job(
    req: Any,  # CreateJobRequest or ProcessJsonRequest
    job: AiJobRecord,
    *,
    background_tasks: Any,  # fastapi.BackgroundTasks
    request_id: Optional[str] = None,
    raw_bytes: bytes = b"",
    raw_inputs: Optional[list[Dict[str, Any]]] = None,
    raw_text: str = "",
    file_type: str = "text",
    metadata: Optional[Dict[str, Any]] = None,
    audio_format: Optional[str] = None,
    transcribe_options: Optional[Dict[str, Any]] = None,
) -> None:
    """Centralized job manifest persistence, initial state prep, caching, and dispatch."""
    from fastapi import HTTPException
    from app.store.models import SourceDocumentRecord
    
    stores = get_stores()

    # 1. Persist source-document manifest to ai_source_documents
    doc_records = []
    source_docs_payload = []
    
    # We duck-type req.source_documents. Each element may be a Pydantic model or dict.
    req_docs = getattr(req, "source_documents", []) or []
    for idx, d in enumerate(req_docs):
        def value(name: str) -> Any:
            return d.get(name) if isinstance(d, dict) else getattr(d, name, None)

        d_id = value("document_id")
        filename = value("filename")
        f_type = value("file_type")
        mime = value("mime_type")
        s_key = value("storage_key")
        f_url = value("file_url")
        h = value("hash") or value("sha256_hash")
        p_count = value("page_count")
        
        doc_rec = SourceDocumentRecord(
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            backend_document_id=d_id,
            source_type=f_type or ("audio" if file_type == "audio" else "text"),
            file_name=filename or d_id or "unknown",
            mime_type=mime or "application/octet-stream",
            storage_key=s_key,
            file_url=f_url,
            sha256_hash=h,
            language=job.options.language,
            page_count=p_count,
        )
        doc_records.append(doc_rec)
        source_docs_payload.append({
            "document_id": d_id,
            "filename": filename,
            "file_type": f_type,
            "mime_type": mime,
            "storage_key": s_key,
            "file_url": f_url,
            "sha256_hash": h,
            "page_count": p_count,
        })

    try:
        if doc_records:
            await stores.chunks.save_documents(doc_records)
    except Exception as exc:
        logger.error("Failed to save source documents manifest for job %s: %s", job.job_id, exc)
        await stores.jobs.set_status(
            job.job_id, JobStatus.FAILED, current_node="failed", progress_pct=100,
            error_code="SOURCE_MANIFEST_PERSIST_FAILED", error_message=str(exc)
        )
        await stores.jobs.add_event(
            JobEventRecord(
                job_id=job.job_id,
                event_type="DISPATCH_FAILED",
                severity="error",
                message=f"SOURCE_MANIFEST_PERSIST_FAILED: {exc}",
                metadata={"error_code": "SOURCE_MANIFEST_PERSIST_FAILED", "request_id": request_id}
            )
        )
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Failed to persist source document manifest.",
                "error_code": "SOURCE_MANIFEST_PERSIST_FAILED",
                "job_id": job.job_id,
            }
        )

    # 2. Build initial state
    initial_state = make_initial_state(
        job.job_id,
        raw_bytes=raw_bytes,
        raw_inputs=raw_inputs,
        raw_text=raw_text,
        file_type=file_type,
        metadata=metadata or {},
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        enable_embeddings=job.options.enable_embeddings,
        enable_hybrid_retrieval=job.options.enable_hybrid_retrieval,
        source_documents=source_docs_payload,
        audio_format=audio_format,
        language=job.options.language,
        transcribe_options=transcribe_options or {},
    )

    # 3. Cache input
    cache_input = {
        "raw_text": raw_text,
        "raw_bytes": raw_bytes,
        "raw_inputs": raw_inputs or [],
        "file_type": file_type,
        "metadata": metadata or {},
        "source_documents": source_docs_payload,
        "audio_format": audio_format,
        "language": job.options.language,
        "transcribe_options": transcribe_options or {},
    }

    # 4. Dispatch the job
    try:
        await dispatch_job(
            job.job_id,
            initial_state=initial_state,
            pipeline=resolve_pipeline(),
            background_tasks=background_tasks,
            request_id=request_id,
            cache_input=cache_input,
        )
    except Exception as exc:
        code = "INPUT_CACHE_FAILED" if "INPUT_CACHE_FAILED" in str(exc) else "QUEUE_DISPATCH_FAILED"
        status_code = 503 if code == "QUEUE_DISPATCH_FAILED" else 500
        message = str(exc)
        logger.error("Failed to dispatch job %s: %s", job.job_id, message)
        
        await stores.jobs.set_status(
            job.job_id, JobStatus.FAILED, current_node="failed", progress_pct=100,
            error_code=code, error_message=message,
        )
        await stores.jobs.add_event(
            JobEventRecord(
                job_id=job.job_id,
                event_type="DISPATCH_FAILED",
                severity="error",
                message=message,
                metadata={"error_code": code, "request_id": request_id},
            )
        )
        raise HTTPException(
            status_code=status_code,
            detail={
                "message": "Failed to dispatch AI job to the queue.",
                "error_code": code,
                "job_id": job.job_id,
            }
        )

