"""
Internal production job API (``/internal/*``).

All routes require the internal service token (see :func:`require_internal_auth`).
They are DB-backed: status/result come from the durable store, never from
process memory, so they work across API/worker instances.

Endpoints:
  POST /internal/jobs                    create/enqueue (idempotent by job_id)
  GET  /internal/jobs/{job_id}           durable job status
  GET  /internal/jobs/{job_id}/result    persisted JobResult (409 if incomplete)
  POST /internal/jobs/{job_id}/cancel    request cancellation
  POST /internal/jobs/{job_id}/retry     retry a terminal/failed job (new attempt)
  POST /internal/jobs/{job_id}/callback-test  (guarded diagnostics helper)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

import json
import asyncio
from pydantic import ValidationError

from app.api.deps import get_request_id, require_internal_auth
from app.api.schemas import CreateJobRequest, RegenerateStoryRequest
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from app.api.service import (
    handle_job_creation,
    internal_status_view,
    resolve_pipeline,
)
from app.config import settings
from app.services.job_store import sanitize_job_id
from app.store.factory import get_stores
from app.store.models import JobOptions, JobStatus, RETRYABLE_JOB_STATUSES, TERMINAL_JOB_STATUSES
from app.worker.dispatch import dispatch_job
from app.worker.state import build_worker_initial_state, make_initial_state

logger = logging.getLogger("app.api.internal")

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_auth)])


def _links(job_id: str) -> Dict[str, str]:
    return {
        "self": f"/internal/jobs/{job_id}",
        "result": f"/internal/jobs/{job_id}/result",
        "cancel": f"/internal/jobs/{job_id}/cancel",
        "retry": f"/internal/jobs/{job_id}/retry",
    }


def _options_from_request(req: CreateJobRequest) -> JobOptions:
    o = req.options
    return JobOptions(
        generate_user_stories=o.generate_user_stories,
        generate_summary=o.generate_summary,
        enable_embeddings=o.enable_embeddings,
        enable_hybrid_retrieval=o.enable_hybrid_retrieval,
        language=o.language,
        callback_url=o.callback_url,
        priority=o.priority,
    )


async def _build_state_and_cache(req: CreateJobRequest, job) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the in-process initial state and the Redis cache_input for a job."""
    metadata: Dict[str, Any] = {
        "tenant_id": req.tenant_id,
        "project_id": req.project_id,
    }
    source_docs_payload: List[Dict[str, Any]] = [
        {
            "document_id": d.document_id,
            "file_type": d.file_type,
            "mime_type": d.mime_type,
            "storage_key": d.storage_key,
            "file_url": d.file_url,
            "sha256_hash": d.hash,
            "page_count": d.page_count,
        }
        for d in req.source_documents
    ]

    raw_text = ""
    file_type = "text"
    if req.input_type in ("text", "backend_transcript"):
        raw_text = req.content or ""
        file_type = "text" if req.input_type == "text" else "transcript"
        if req.source_documents:
            metadata["filename"] = req.source_documents[0].document_id
    else:  # backend_document / backend_audio
        file_type = "document" if req.input_type == "backend_document" else "audio"
        if req.source_documents:
            metadata["filename"] = req.source_documents[0].document_id
        # In-process (no worker): fetch backend text now so the job can run here.
        if not settings.use_redis_queue:
            from app.clients.backend import BackendDocumentClient

            client = BackendDocumentClient()
            texts: List[str] = []
            for ref in source_docs_payload:
                text = await client.fetch_document_text(ref)
                if text:
                    texts.append(text)
            if texts:
                raw_text = "\n\n".join(texts)
                file_type = "text"

    initial_state = make_initial_state(
        job.job_id,
        raw_text=raw_text,
        file_type=file_type,
        metadata=metadata,
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        enable_embeddings=req.options.enable_embeddings,
        enable_hybrid_retrieval=req.options.enable_hybrid_retrieval,
    )
    cache_input = {
        "raw_text": req.content or "",
        "file_type": file_type,
        "metadata": metadata,
        "source_documents": source_docs_payload,
    }
    return initial_state, cache_input


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    req: CreateJobRequest,
    background_tasks: BackgroundTasks,
    request: Request,
):
    """Create/enqueue a job — idempotent and race-safe by job_id.

    A repeated ``job_id`` is handled per the request's fingerprint (production-
    relevant fields only, see ``app.services.fingerprint``) and the existing
    job's current status. See ``handle_job_creation`` for the full matrix:
    running+same-payload -> 202 idempotent; running+different-payload -> 409;
    completed/partial/rejected+same-payload -> 200 idempotent; failed/
    cancelled+same-payload -> 200 (report only) unless ``reprocess=true``
    (matching payload) -> 202 new attempt; any payload mismatch -> 409.
    Concurrent identical requests race-safely produce exactly one dispatch.
    """
    request_id = get_request_id(request)

    # Validate job id.
    try:
        job_id = sanitize_job_id(req.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid job_id: {exc}") from None

    # Input-type-specific validation.
    if req.input_type in ("text", "backend_transcript"):
        if not (req.content and req.content.strip()):
            raise HTTPException(
                status_code=400,
                detail=f"content is required for input_type '{req.input_type}'",
            )
    else:  # backend_document / backend_audio
        if not req.source_documents:
            raise HTTPException(
                status_code=400,
                detail=f"source_documents is required for input_type '{req.input_type}'",
            )

    outcome = await handle_job_creation(req, job_id=job_id, request_id=request_id)

    if not outcome.dispatch:
        logger.info(
            "job_id=%s duplicate/conflict handling -> http_status=%s idempotent=%s request_id=%s",
            job_id, outcome.http_status, outcome.body.get("idempotent"), request_id,
        )
        return JSONResponse(status_code=outcome.http_status, content=outcome.body)

    rec = outcome.job
    initial_state, cache_input = await _build_state_and_cache(req, rec)
    await dispatch_job(
        job_id,
        initial_state=initial_state,
        pipeline=resolve_pipeline(),
        background_tasks=background_tasks,
        request_id=request_id,
        cache_input=cache_input,
    )
    logger.info(
        "job dispatched job_id=%s tenant=%s project=%s input_type=%s attempt=%s request_id=%s",
        job_id, req.tenant_id, req.project_id, req.input_type, rec.attempt_number, request_id,
    )
    return JSONResponse(status_code=outcome.http_status, content=outcome.body)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    view = await internal_status_view(job_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return view


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str):
    stores = get_stores()
    rec = await stores.jobs.get_job(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Job not found")
    result = await stores.results.get_result(job_id)
    if result is None:
        # Not complete yet (or failed with no result) — clear 409 with status.
        raise HTTPException(
            status_code=409,
            detail={
                "message": "result not available yet",
                "job_id": job_id,
                "status": rec.status.value,
            },
        )
    return result


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    stores = get_stores()
    rec = await stores.jobs.get_job(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if rec.status in TERMINAL_JOB_STATUSES:
        # Already finished — cannot cancel; report the terminal status.
        return {"job_id": job_id, "status": rec.status.value, "cancelled": False,
                "detail": "job already terminal"}

    # Request cooperative cancellation; if still QUEUED, mark CANCELLED now.
    await stores.jobs.request_cancel(job_id)
    if rec.status == JobStatus.QUEUED:
        await stores.jobs.set_status(job_id, JobStatus.CANCELLED, current_node="cancelled")
        from app.progress import update_progress

        update_progress(job_id, "cancelled", rec.progress_pct, "FAILED",
                        error="JOB_CANCELLED: cancelled while queued")
    return {"job_id": job_id, "status": JobStatus.CANCELLED.value, "cancelled": True}


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_job(job_id: str, background_tasks: BackgroundTasks, request: Request):
    """Retry a failed/cancelled job as a new attempt.

    Only ``FAILED``/``CANCELLED`` jobs are retryable (matches the ``reprocess``
    flag rules on ``POST /internal/jobs``) — a running or already-terminal-
    success job returns 409. The check-and-requeue is atomic (row-locked), so
    two concurrent ``/retry`` calls for the same job can never both dispatch.
    """
    request_id = get_request_id(request)
    stores = get_stores()
    existing = await stores.jobs.get_job(job_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if existing.status not in RETRYABLE_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Cannot retry a job in status {existing.status.value}. "
                    "Retry is only allowed for FAILED or CANCELLED jobs."
                ),
                "status": existing.status.value,
            },
        )

    # Atomic check-and-set against the job's OWN stored fingerprint (there is
    # no new request body here) — a concurrent second /retry call blocks on the
    # row lock, then sees the now-QUEUED status and correctly no-ops below.
    updated = await stores.jobs.try_requeue_for_retry(
        job_id,
        allowed_statuses=RETRYABLE_JOB_STATUSES,
        fingerprint=existing.request_fingerprint,
        options=existing.options,
        callback_url=existing.callback_url,
    )
    if updated is None:
        current = await stores.jobs.get_job(job_id) or existing
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A retry for this job is already in progress or its state changed.",
                "status": current.status.value,
            },
        )

    from app.progress import update_progress

    update_progress(job_id, updated.current_node, updated.progress_pct, updated.status.value)

    # Reconstruct input (redis cache → backend → persisted chunks) so the retry
    # needs no re-upload and does not duplicate source documents.
    initial_state = await build_worker_initial_state(updated, stores)
    await dispatch_job(
        job_id,
        initial_state=initial_state,
        pipeline=resolve_pipeline(),
        background_tasks=background_tasks,
        request_id=request_id,
    )
    logger.info("job retry dispatched job_id=%s attempt=%s request_id=%s",
                job_id, updated.attempt_number, request_id)
    return {"job_id": job_id, "status": JobStatus.QUEUED.value,
            "attempt_number": updated.attempt_number, "links": _links(job_id)}


@router.post("/jobs/{job_id}/callback-test")
async def callback_test(job_id: str, request: Request):
    """Guarded helper: fire a test callback for a job. Disabled in production."""
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not found")
    stores = get_stores()
    rec = await stores.jobs.get_job(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Job not found")
    callback_url = rec.callback_url or rec.options.callback_url
    if not callback_url:
        raise HTTPException(status_code=400, detail="job has no callback_url")
    from app.clients.backend import BackendDocumentClient

    ok = await BackendDocumentClient().send_callback(
        callback_url,
        {"job_id": job_id, "status": rec.status.value, "test": True},
        request_id=get_request_id(request),
    )
    return {"job_id": job_id, "callback_url": callback_url, "delivered": ok}


def _build_regeneration_prompt(req: RegenerateStoryRequest) -> str:
    parts = [
        f"Requirement Text: {req.requirement_text}",
        f"Requirement Type: {req.requirement_type}",
        f"Actor: {req.actor or 'None'}",
        f"Goal: {req.goal or 'None'}",
        f"Priority: {req.priority}",
        f"Human Feedback/Instruction: {req.feedback}"
    ]
    if req.original_story:
        parts.append(f"Original Story (to be refined/improved): {req.original_story}")
    if req.source_context:
        parts.append(f"Source/Business Context: {req.source_context}")
    return "\n".join(parts)


@router.post("/stories/regenerate")
async def regenerate_story(req: RegenerateStoryRequest):
    """Stateless regeneration of a single user story with feedback."""
    llm = get_llm()
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM reasoning service not initialized or API keys missing.")
    
    system_prompt = load_prompt(PromptId.REGENERATE_STORY_V1)
    user_prompt = _build_regeneration_prompt(req)
    
    try:
        timeout = getattr(settings, "PROVIDER_TIMEOUT_SECONDS", 120)
        raw = await asyncio.wait_for(
            llm.ainvoke([
                ("system", system_prompt),
                ("user", user_prompt)
            ]),
            timeout=float(timeout)
        )
        content = getattr(raw, "content", None) or str(raw)
        
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        parsed = json.loads(content)
        from app.api.schemas import RegenerateStoryResponse
        response = RegenerateStoryResponse.model_validate(parsed)
        return response
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="LLM reasoning request timed out.")
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("Failed to parse or validate LLM response for story regeneration: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM response parsing or validation failed: {str(e)}")
    except Exception as e:
        logger.error("Unexpected error in story regeneration: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to regenerate story: {str(e)}")
