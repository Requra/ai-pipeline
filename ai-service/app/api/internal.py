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
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    status,
    UploadFile,
    File,
    Form,
)
from fastapi.responses import JSONResponse

import json
import asyncio
from pydantic import ValidationError

from app.api.deps import get_request_id, require_internal_auth
from app.api.schemas import CreateJobRequest, RegenerateStoryRequest, ProcessJsonRequest
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from app.api.service import (
    handle_job_creation,
    internal_status_view,
    resolve_pipeline,
    prepare_and_dispatch_job,
)
from app.config import settings
from app.services.job_store import sanitize_job_id
from app.store.factory import get_stores
from app.store.models import (
    JobOptions,
    JobStatus,
    RETRYABLE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from app.worker.dispatch import dispatch_job
from app.worker.state import build_worker_initial_state, make_initial_state

logger = logging.getLogger("app.api.internal")

router = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_auth)]
)


from collections import OrderedDict


class BoundedInMemoryStore:
    """Thread-safe bounded memory store for tests/dev to prevent production RAM growth."""

    def __init__(self, max_items: int = 50):
        self._data: OrderedDict[str, bytes] = OrderedDict()
        self._max_items = max_items

    def __setitem__(self, key: str, value: bytes):
        if settings.is_production:
            return
        if len(self._data) >= self._max_items:
            self._data.popitem(last=False)
        self._data[key] = value

    def __getitem__(self, key: str) -> bytes:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        if settings.is_production:
            return False
        return key in self._data

    def get(self, key: str, default: Optional[bytes] = None) -> Optional[bytes]:
        if settings.is_production:
            return default
        return self._data.get(key, default)

    def clear(self):
        self._data.clear()


MOCK_DOCUMENT_STORAGE = BoundedInMemoryStore(max_items=50)


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


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    req: CreateJobRequest,
    background_tasks: BackgroundTasks,
    request: Request,
):
    """Create/enqueue a job — idempotent and race-safe by job_id."""
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
            job_id,
            outcome.http_status,
            outcome.body.get("idempotent"),
            request_id,
        )
        return JSONResponse(status_code=outcome.http_status, content=outcome.body)

    rec = outcome.job

    # Resolve inputs for prepare_and_dispatch_job
    raw_bytes = b""
    raw_text = ""
    file_type = "text"
    audio_format = None
    transcribe_options = {}

    if req.input_type in ("text", "backend_transcript"):
        raw_text = req.content or ""
        file_type = "text" if req.input_type == "text" else "transcript"
    else:  # backend_document / backend_audio
        file_type = "document" if req.input_type == "backend_document" else "audio"

    await prepare_and_dispatch_job(
        req,
        rec,
        background_tasks=background_tasks,
        request_id=request_id,
        raw_bytes=raw_bytes,
        raw_text=raw_text,
        file_type=file_type,
        metadata={"tenant_id": req.tenant_id, "project_id": req.project_id},
        audio_format=audio_format,
        transcribe_options=transcribe_options,
    )

    logger.info(
        "job dispatched job_id=%s tenant=%s project=%s input_type=%s attempt=%s request_id=%s",
        job_id,
        req.tenant_id,
        req.project_id,
        req.input_type,
        rec.attempt_number,
        request_id,
    )
    return JSONResponse(
        status_code=outcome.http_status,
        content=outcome.body,
        background=background_tasks,
    )


@router.post("/process", status_code=status.HTTP_202_ACCEPTED)
async def process_compatibility(
    background_tasks: BackgroundTasks,
    request: Request,
    files: List[UploadFile] = File(default=[]),
    # Legacy singular field retained for existing clients. New callers should
    # submit one or more repeated `files` parts.
    file: Optional[UploadFile] = File(None),
    job_id: str = Form(...),
    project_id: str = Form(...),
    tenant_id: Optional[str] = Form(None),
    requested_by: Optional[str] = Form(None),
    document_id: Optional[str] = Form(None),
    document_ids: List[str] = Form(default=[]),
    metadata: str = Form("{}"),
    callback_url: Optional[str] = Form(None),
    language: Optional[str] = Form("en"),
    reprocess: bool = Form(False),
):
    """File/audio compatibility upload endpoint (multipart/form-data)."""
    request_id = get_request_id(request)

    # 1. Parse metadata JSON
    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {exc.msg}")
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")

    # 2. Validate job ID
    try:
        sanitized_job_id = sanitize_job_id(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid job_id: {exc}") from None

    # 3. Validate project ID
    if not project_id or not project_id.strip():
        raise HTTPException(status_code=400, detail="project_id is required")

    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid multipart form data: {exc}"
        ) from None

    form_file_items = [
        v
        for k, v in form.multi_items()
        if k == "file" and (isinstance(v, UploadFile) or hasattr(v, "filename"))
    ]
    form_files_items = [
        v
        for k, v in form.multi_items()
        if k == "files" and (isinstance(v, UploadFile) or hasattr(v, "filename"))
    ]

    if (file is not None and bool(files)) or (form_file_items and form_files_items):
        raise HTTPException(
            status_code=400,
            detail="use either the legacy 'file' field or repeated 'files' fields, not both",
        )

    if form_files_items:
        uploads = form_files_items
    elif form_file_items:
        uploads = form_file_items
    else:
        uploads = list(files)
        if file is not None:
            uploads.append(file)

    if not uploads:
        raise HTTPException(
            status_code=400, detail="at least one file upload is required"
        )

    if len(uploads) > settings.MAX_SOURCES_PER_JOB:
        raise HTTPException(
            status_code=400,
            detail=f"Too many uploaded files ({len(uploads)} > maximum allowed {settings.MAX_SOURCES_PER_JOB})",
        )

    form_doc_id_items = [
        v
        for k, v in form.multi_items()
        if k == "document_id" and isinstance(v, str) and v.strip()
    ]
    form_doc_ids_items = [
        v
        for k, v in form.multi_items()
        if k == "document_ids" and isinstance(v, str) and v.strip()
    ]

    if form_doc_id_items and form_doc_ids_items:
        raise HTTPException(
            status_code=400,
            detail="use either legacy document_id or repeated document_ids, not both",
        )

    resolved_document_ids: List[str] = []
    if form_doc_ids_items:
        resolved_document_ids = form_doc_ids_items
    elif len(form_doc_id_items) > 1:
        resolved_document_ids = form_doc_id_items
    elif document_ids:
        resolved_document_ids = document_ids
    elif form_doc_id_items:
        resolved_document_ids = form_doc_id_items
    elif document_id:
        resolved_document_ids = [document_id]

    if resolved_document_ids:
        if len(resolved_document_ids) != len(uploads):
            if len(uploads) == 1 and len(resolved_document_ids) == 1:
                pass
            else:
                raise HTTPException(
                    status_code=400,
                    detail="document_ids must be repeated once for each uploaded file, in files order",
                )
    elif document_id and len(uploads) != 1:
        raise HTTPException(
            status_code=400,
            detail="legacy document_id is only valid with a single uploaded file; use document_ids for multiple files",
        )

    # 4. Stream and validate each input safely without unbounded memory allocation.
    from app.services.file_inspection import detect_mime_and_type
    import hashlib
    import io

    total_aggregate_bytes = 0
    validated_inputs: List[Dict[str, Any]] = []
    for index, upload in enumerate(uploads):
        hasher = hashlib.sha256()
        buffer = io.BytesIO()
        file_size = 0
        filename = upload.filename or f"upload-{index + 1}"

        # Stream incrementally in 64 KB blocks
        while True:
            chunk = await upload.read(65536)
            if not chunk:
                break
            file_size += len(chunk)
            total_aggregate_bytes += len(chunk)
            if total_aggregate_bytes > settings.MAX_TOTAL_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Aggregate upload size exceeds limit of {settings.MAX_TOTAL_UPLOAD_BYTES} bytes",
                )
            buffer.write(chunk)
            hasher.update(chunk)

        file_bytes = buffer.getvalue()
        if file_size == 0:
            raise HTTPException(status_code=400, detail=f"file '{filename}' is empty")

        file_type, mime_type, subtype = detect_mime_and_type(file_bytes, filename)
        if file_type == "unknown":
            raise HTTPException(
                status_code=415,
                detail=f"file '{filename}' has an unsupported media type or unrecognized signature",
            )
        limit = (
            settings.MAX_AUDIO_BYTES
            if file_type == "audio"
            else settings.MAX_DOCUMENT_BYTES
        )
        if file_size > limit:
            kind = "Audio" if file_type == "audio" else "Document"
            raise HTTPException(
                status_code=413,
                detail=f"{kind} file '{filename}' is too large ({file_size} > {limit})",
            )

        file_hash = hasher.hexdigest()
        supplied_id = document_ids[index] if document_ids else document_id
        if supplied_id is not None and not supplied_id.strip():
            raise HTTPException(
                status_code=400, detail="document IDs must be non-empty when supplied"
            )
        resolved_doc_id = supplied_id or f"doc_{file_hash[:16]}"
        validated_inputs.append(
            {
                "document_id": resolved_doc_id,
                "filename": filename,
                "file_type": file_type,
                "mime_type": mime_type,
                "sha256_hash": file_hash,
                "audio_format": subtype if file_type == "audio" else None,
                "raw_bytes": file_bytes,
            }
        )

    resolved_ids = [item["document_id"] for item in validated_inputs]
    if len(set(resolved_ids)) != len(resolved_ids):
        raise HTTPException(
            status_code=400,
            detail="each uploaded file must have a unique document ID; supply distinct document_ids for duplicate content",
        )

    audio_count = sum(1 for item in validated_inputs if item["file_type"] == "audio")
    doc_count = sum(1 for item in validated_inputs if item["file_type"] != "audio")
    if audio_count > settings.MAX_AUDIO_SOURCES_PER_JOB:
        raise HTTPException(
            status_code=400,
            detail=f"multiple audio uploads are not supported; submit at most {settings.MAX_AUDIO_SOURCES_PER_JOB} audio file per job",
        )

    if audio_count > 0 and doc_count > 0:
        if not getattr(settings, "ENABLE_MIXED_SOURCE_JOBS", True):
            raise HTTPException(
                status_code=400,
                detail="Mixed document and audio source jobs are disabled by ENABLE_MIXED_SOURCE_JOBS",
            )
        mapped_input_type = "backend_sources"
    elif audio_count > 0:
        mapped_input_type = "backend_audio"
    else:
        mapped_input_type = "backend_document"

    from app.api.schemas import SourceDocumentIn, CreateJobRequest, JobOptionsIn

    source_docs = [
        SourceDocumentIn(
            document_id=item["document_id"],
            filename=item["filename"],
            file_type=item["file_type"],
            mime_type=item["mime_type"],
            storage_key=None,
            file_url=None,
            sha256_hash=item["sha256_hash"],
            page_count=None,
        )
        for item in validated_inputs
    ]

    req = CreateJobRequest(
        job_id=sanitized_job_id,
        tenant_id=tenant_id,
        project_id=project_id,
        requested_by=requested_by,
        input_type=mapped_input_type,
        source_documents=source_docs,
        content=None,
        options=JobOptionsIn(
            generate_user_stories=True,
            generate_summary=True,
            enable_embeddings=False,
            enable_hybrid_retrieval=False,
            language=language or "en",
            callback_url=callback_url,
            priority="normal",
        ),
        reprocess=reprocess,
    )

    outcome = await handle_job_creation(
        req, job_id=sanitized_job_id, request_id=request_id
    )
    if not outcome.dispatch:
        return JSONResponse(status_code=outcome.http_status, content=outcome.body)

    rec = outcome.job
    # Cache raw bytes locally for the compatibility recovery endpoint / tests.
    for item in validated_inputs:
        MOCK_DOCUMENT_STORAGE[item["document_id"]] = item["raw_bytes"]

    single_input = validated_inputs[0] if len(validated_inputs) == 1 else None
    is_single_source = len(validated_inputs) == 1
    found_audio_fmt = next(
        (item["audio_format"] for item in validated_inputs if item.get("audio_format")),
        None,
    )

    await prepare_and_dispatch_job(
        req,
        rec,
        background_tasks=background_tasks,
        request_id=request_id,
        raw_bytes=(
            single_input["raw_bytes"] if is_single_source and single_input else b""
        ),
        raw_inputs=validated_inputs,
        raw_text="",
        file_type=(
            single_input["file_type"]
            if single_input
            else ("sources" if mapped_input_type == "backend_sources" else "document")
        ),
        metadata=parsed_metadata,
        audio_format=single_input["audio_format"] if single_input else found_audio_fmt,
        transcribe_options={},
    )

    return JSONResponse(
        status_code=outcome.http_status,
        content=outcome.body,
        background=background_tasks,
    )


@router.post("/process-json", status_code=status.HTTP_202_ACCEPTED)
async def process_json_compatibility(
    req: ProcessJsonRequest,
    background_tasks: BackgroundTasks,
    request: Request,
):
    """Text/transcript compatibility submission endpoint (application/json)."""
    request_id = get_request_id(request)

    # 1. Validate job ID
    try:
        sanitized_job_id = sanitize_job_id(req.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid job_id: {exc}") from None

    # 2. Validate project ID
    if not req.project_id or not req.project_id.strip():
        raise HTTPException(status_code=400, detail="project_id is required")

    # 3. Validate content
    if not req.content or not req.content.strip():
        raise HTTPException(status_code=400, detail="content is required")

    # 4. Map source type
    mapped_input_type = "text" if req.source_type == "text" else "backend_transcript"
    file_type = "text" if req.source_type == "text" else "transcript"

    from app.api.schemas import CreateJobRequest

    canonical_req = CreateJobRequest(
        job_id=sanitized_job_id,
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        requested_by=req.requested_by,
        input_type=mapped_input_type,
        source_documents=req.source_documents,
        content=req.content,
        options=req.options,
        reprocess=req.reprocess,
    )

    outcome = await handle_job_creation(
        canonical_req, job_id=sanitized_job_id, request_id=request_id
    )
    if not outcome.dispatch:
        return JSONResponse(status_code=outcome.http_status, content=outcome.body)

    rec = outcome.job
    await prepare_and_dispatch_job(
        canonical_req,
        rec,
        background_tasks=background_tasks,
        request_id=request_id,
        raw_bytes=b"",
        raw_text=req.content,
        file_type=file_type,
        metadata=req.metadata,
        audio_format=None,
        transcribe_options={},
    )

    return JSONResponse(
        status_code=outcome.http_status,
        content=outcome.body,
        background=background_tasks,
    )


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
        return {
            "job_id": job_id,
            "status": rec.status.value,
            "cancelled": False,
            "detail": "job already terminal",
        }

    # Request cooperative cancellation; if still QUEUED, mark CANCELLED now.
    await stores.jobs.request_cancel(job_id)
    if rec.status == JobStatus.QUEUED:
        await stores.jobs.set_status(
            job_id, JobStatus.CANCELLED, current_node="cancelled"
        )
        from app.progress import update_progress

        update_progress(
            job_id,
            "cancelled",
            rec.progress_pct,
            "FAILED",
            error="JOB_CANCELLED: cancelled while queued",
        )
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

    update_progress(
        job_id, updated.current_node, updated.progress_pct, updated.status.value
    )

    # Reconstruct input: the worker or in-process queue factory will build this state asynchronously.
    await dispatch_job(
        job_id,
        initial_state=None,
        pipeline=resolve_pipeline(),
        background_tasks=background_tasks,
        request_id=request_id,
    )
    logger.info(
        "job retry dispatched job_id=%s attempt=%s request_id=%s",
        job_id,
        updated.attempt_number,
        request_id,
    )
    return {
        "job_id": job_id,
        "status": JobStatus.QUEUED.value,
        "attempt_number": updated.attempt_number,
        "links": _links(job_id),
    }


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
        f"Human Feedback/Instruction: {req.feedback}",
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
        raise HTTPException(
            status_code=503,
            detail="LLM reasoning service not initialized or API keys missing.",
        )

    system_prompt = load_prompt(PromptId.REGENERATE_STORY_V1)
    user_prompt = _build_regeneration_prompt(req)

    try:
        timeout = getattr(settings, "PROVIDER_TIMEOUT_SECONDS", 120)
        raw = await asyncio.wait_for(
            llm.ainvoke([("system", system_prompt), ("user", user_prompt)]),
            timeout=float(timeout),
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
        logger.error(
            "Failed to parse or validate LLM response for story regeneration: %s", e
        )
        raise HTTPException(
            status_code=502,
            detail=f"LLM response parsing or validation failed: {str(e)}",
        )
    except Exception as e:
        logger.error("Unexpected error in story regeneration: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to regenerate story: {str(e)}"
        )


@router.get("/documents/{document_id}/content")
async def get_document_content(document_id: str):
    """Retrieve raw document content by its document ID."""
    from fastapi.responses import Response

    # 1. Check local mock storage first (for mock/test uploads)
    if document_id in MOCK_DOCUMENT_STORAGE:
        content_bytes = MOCK_DOCUMENT_STORAGE[document_id]
        from app.services.file_inspection import detect_mime_and_type

        _, mime_type, _ = detect_mime_and_type(content_bytes)
        return Response(content=content_bytes, media_type=mime_type)

    # 2. Lookup in durable database
    stores = get_stores()
    doc = await stores.chunks.get_document_by_backend_id(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # 3. Check Redis cache for stashed bytes if no URL
    if not doc.file_url:
        from app.worker.state import load_input

        cached = load_input(doc.job_id)
        if cached:
            for cached_input in cached.get("raw_inputs", []) or []:
                if cached_input.get("document_id") == document_id:
                    import base64

                    content_bytes = base64.b64decode(
                        cached_input.get("raw_bytes_b64", "") or ""
                    )
                    return Response(
                        content=content_bytes,
                        media_type=doc.mime_type or "application/octet-stream",
                    )
            b64 = cached.get("raw_bytes_b64", "")
            if b64:
                import base64

                content_bytes = base64.b64decode(b64)
                return Response(
                    content=content_bytes,
                    media_type=doc.mime_type or "application/octet-stream",
                )

    # 4. If URL exists, fetch the bytes using the secure fetcher
    if doc.file_url:
        from app.clients.backend import BackendDocumentClient, SourceDownloadError

        client = BackendDocumentClient()
        try:
            content_bytes = await client.fetch_document_bytes(
                {
                    "document_id": doc.backend_document_id,
                    "file_type": doc.source_type,
                    "mime_type": doc.mime_type,
                    "storage_key": doc.storage_key,
                    "file_url": doc.file_url,
                    "sha256_hash": doc.sha256_hash,
                }
            )
            return Response(
                content=content_bytes,
                media_type=doc.mime_type or "application/octet-stream",
            )
        except SourceDownloadError as e:
            raise HTTPException(
                status_code=400, detail=f"Source download failed: {str(e)}"
            )

    raise HTTPException(
        status_code=404, detail="Raw content is not cached or reachable"
    )
