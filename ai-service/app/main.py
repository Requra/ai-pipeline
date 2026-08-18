"""
FastAPI surface for the AI pipeline service.

Public (backward-compatible, demo/dev) contract:

  POST /process         (multipart/form-data)   — demo/dev-compatible
  POST /process-json    (application/json)       — demo/dev-compatible
  GET  /status/{job_id}
  GET  /health
  GET  /ready

Production internal contract (service-token protected — see app.api.internal):

  POST /internal/jobs
  GET  /internal/jobs/{job_id}
  GET  /internal/jobs/{job_id}/result
  POST /internal/jobs/{job_id}/cancel
  POST /internal/jobs/{job_id}/retry
  POST /internal/jobs/{job_id}/callback-test

Internally, *all* entry points create a durable DB-backed job and dispatch it
through the same queue/worker path (in-process by default; Redis/RQ in
production). ``/process`` and ``/process-json`` are marked demo/dev-compatible:
they accept content directly and hold it in memory for the run, but the AI
service never becomes the owner of the original uploaded file bytes.

Error policy:
  * 400 — caller bug (empty content, invalid metadata JSON, etc.).
  * 401/403 — missing/invalid internal service token (/internal/*).
  * 404 — unknown job id.
  * 409 — result requested before completion.
  * 413 — file too large.
  * 415 — unsupported content type.
  * 500 — unexpected server bug. Detail is safe text, never a stack trace.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.api.deps import REQUEST_ID_HEADER
from app.api.internal import router as internal_router
from app.api.service import get_or_create_job, public_status_view
from app.graph.pipeline import build_pipeline
from app.services.job_store import sanitize_job_id
from app.startup import build_readiness_report, run_startup_checks
from app.store.factory import close_stores
from app.store.models import JobOptions
from app.worker.dispatch import dispatch_job
from app.worker.state import make_initial_state

logger = logging.getLogger("app.main")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    run_startup_checks()
    try:
        yield
    finally:
        await close_stores()


app = FastAPI(
    title="AI Service Pipeline",
    description="LangGraph execution microservice",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request tracing — X-Request-Id in/out + safe access log (never logs bodies).
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_tracing_middleware(request: Request, call_next):
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
    request.state.request_id = request_id
    start = time.time()
    response = await call_next(request)
    duration_ms = int((time.time() - start) * 1000)
    response.headers[REQUEST_ID_HEADER] = request_id
    # Safe access log: method, path, status, duration, request id — no bodies,
    # no query values, no headers, so raw content never leaks to logs.
    logger.info(
        "%s %s -> %s (%dms) request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


# ---------------------------------------------------------------------------
# CORS — env-driven, no wildcard+credentials in production.
# ---------------------------------------------------------------------------


def _resolve_cors_origins() -> list[str]:
    """
    Resolve allowed origins from `ALLOWED_ORIGINS` env (comma-separated).

    Behaviour:
      * `ALLOWED_ORIGINS` set → use it verbatim. Wildcard `*` in production is
        downgraded to the default dev list (wildcard+credentials is unsafe).
      * Unset and `ENV != production` → sensible local-dev list.
      * Unset and `ENV=production` → empty list (operators must opt in).
    """
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
    env = os.environ.get("ENV", "development").strip().lower()
    is_production = env in {"production", "prod"}

    if raw:
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if is_production and "*" in items:
            return _default_dev_origins()
        return items

    if is_production:
        return []

    return _default_dev_origins()


def _default_dev_origins() -> list[str]:
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


ALLOWED_ORIGINS = _resolve_cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the production internal API (service-token protected).
app.include_router(internal_router)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Liveness probe for Docker/monitoring — lightweight, no dependencies."""
    return {"status": "healthy", "service": "ai-pipeline"}


# ---------------------------------------------------------------------------
# Dev Mock Document Endpoints
# ---------------------------------------------------------------------------


@app.get("/mock-doc-a")
def get_mock_doc_a():
    """Returns mock PDF pages separated by Form-Feed (\\f)."""
    content = "This is Page 1 content containing authentication requirements.\\fThis is Page 2 content detailing validation schemas."
    return Response(content=content, media_type="text/plain")


@app.get("/mock-doc-b")
def get_mock_doc_b():
    """Returns mock plain text."""
    content = "The system must process card payments and return a confirmation code."
    return Response(content=content, media_type="text/plain")


@app.get("/ready")
async def readiness_check():
    """Readiness probe with safe diagnostics (no secrets ever leave this)."""
    report = await build_readiness_report()
    status_code = 200 if report.get("ready") else 503
    return JSONResponse(status_code=status_code, content=report)


# ---------------------------------------------------------------------------
# Pipeline + request models
# ---------------------------------------------------------------------------

# Module-global compiled pipeline. Kept as a module attribute (not hidden behind
# a factory) so tests can patch `app.main.pipeline` to inject a mock, and the
# demo dispatch path picks up the patched value at call time.
pipeline = build_pipeline()


class ProcessRequest(BaseModel):
    job_id: Optional[str] = None
    content: str
    source_type: Optional[str] = "multi_document"
    source_documents: Optional[list] = []
    metadata: dict = {}


ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/x-m4a",
    "audio/m4a",
    "audio/webm",
    "audio/mp3",
    "audio/x-mp3",
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@app.get("/status/{job_id}")
async def get_job_status(job_id: str):
    view = await public_status_view(job_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return view


# ---------------------------------------------------------------------------
# /process — multipart file upload (demo/dev-compatible)
# ---------------------------------------------------------------------------


@app.post("/process", status_code=202)
async def process_document(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(default=[]),
    file: Optional[UploadFile] = File(None),
    document_ids: List[str] = Form(default=[]),
    document_id: Optional[str] = Form(None),
    metadata: str = Form("{}"),
    file_type: str = Form("document"),
):
    from app.services.file_inspection import detect_mime_and_type
    import hashlib
    import io

    # Parse form safely to extract all uploaded parts even if repeated under 'file' or 'files'
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

    max_sources = getattr(settings, "MAX_SOURCES_PER_JOB", 20)
    if len(uploads) > max_sources:
        raise HTTPException(
            status_code=400,
            detail=f"Too many uploaded files ({len(uploads)} > maximum allowed {max_sources})",
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

    # ---- Metadata JSON guard (400, not 500) ----------------------------
    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid metadata JSON: {exc.msg}"
        ) from None
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")

    max_total_bytes = getattr(settings, "MAX_TOTAL_UPLOAD_BYTES", 100 * 1024 * 1024)
    total_aggregate_bytes = 0
    validated_inputs: List[Dict[str, Any]] = []

    for index, upload in enumerate(uploads):
        hasher = hashlib.sha256()
        buffer = io.BytesIO()
        file_size = 0
        filename = upload.filename or f"upload-{index + 1}"

        while True:
            chunk = await upload.read(65536)
            if not chunk:
                break
            file_size += len(chunk)
            total_aggregate_bytes += len(chunk)
            if total_aggregate_bytes > max_total_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Aggregate upload size exceeds limit of {max_total_bytes} bytes",
                )
            buffer.write(chunk)
            hasher.update(chunk)

        file_bytes = buffer.getvalue()
        if file_size == 0:
            raise HTTPException(status_code=400, detail=f"file '{filename}' is empty")

        det_file_type, det_mime_type, det_subtype = detect_mime_and_type(
            file_bytes, filename
        )
        if det_file_type == "unknown":
            raise HTTPException(
                status_code=415,
                detail=f"file '{filename}' has an unsupported media type or unrecognized signature",
            )

        limit = (
            settings.MAX_AUDIO_BYTES
            if det_file_type == "audio"
            else settings.MAX_DOCUMENT_BYTES
        )
        if file_size > limit:
            kind = "Audio" if det_file_type == "audio" else "Document"
            raise HTTPException(
                status_code=413,
                detail=f"{kind} file '{filename}' is too large ({file_size} > {limit})",
            )

        file_hash = hasher.hexdigest()
        supplied_id = (
            resolved_document_ids[index] if index < len(resolved_document_ids) else None
        )
        resolved_doc_id = supplied_id or f"doc_{file_hash[:16]}"

        validated_inputs.append(
            {
                "document_id": resolved_doc_id,
                "filename": filename,
                "file_type": det_file_type,
                "mime_type": det_mime_type,
                "sha256_hash": file_hash,
                "audio_format": det_subtype if det_file_type == "audio" else None,
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

    max_audio = getattr(settings, "MAX_AUDIO_SOURCES_PER_JOB", 3)
    if audio_count > max_audio:
        raise HTTPException(
            status_code=400,
            detail=f"Too many audio files ({audio_count} > maximum allowed {max_audio})",
        )

    from app.services.source_processing.audio import get_audio_duration_seconds
    total_audio_duration = 0.0
    max_total_audio = getattr(settings, "MAX_TOTAL_AUDIO_DURATION_SECONDS", 5400)
    for item in validated_inputs:
        if item["file_type"] == "audio":
            dur = get_audio_duration_seconds(item.get("raw_bytes") or b"", item.get("audio_format") or "mp3")
            if dur is not None:
                total_audio_duration += dur

    if total_audio_duration > max_total_audio:
        raise HTTPException(
            status_code=400,
            detail=f"Aggregate audio duration ({total_audio_duration:.1f}s) exceeds limit of {max_total_audio}s",
        )

    if (audio_count > 0 and doc_count > 0) or audio_count > 1:
        if audio_count > 0 and doc_count > 0 and not getattr(settings, "ENABLE_MIXED_SOURCE_JOBS", True):
            raise HTTPException(
                status_code=400,
                detail="Mixed document and audio source jobs are disabled by ENABLE_MIXED_SOURCE_JOBS",
            )
        mapped_input_type = "backend_sources"
        dispatched_file_type = "sources"
    elif audio_count == 1:
        mapped_input_type = "backend_audio"
        dispatched_file_type = "audio"
    else:
        mapped_input_type = "backend_document"
        dispatched_file_type = "document"

    source_docs = [
        {
            "document_id": item["document_id"],
            "filename": item["filename"],
            "file_type": item["file_type"],
            "mime_type": item["mime_type"],
            "sha256_hash": item["sha256_hash"],
        }
        for item in validated_inputs
    ]

    parsed_metadata.setdefault("filename", validated_inputs[0]["filename"])
    if len(validated_inputs) > 1:
        parsed_metadata["source_count"] = len(validated_inputs)

    # ---- Stable job id: uuid4, or a validated caller-supplied one ------
    provided_job_id = parsed_metadata.get("job_id")
    if provided_job_id:
        try:
            job_id = sanitize_job_id(str(provided_job_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid job_id in metadata: {exc}"
            ) from None
    else:
        job_id = f"upload_{uuid.uuid4().hex}"

    is_single_source = len(validated_inputs) == 1
    found_audio_fmt = next(
        (item["audio_format"] for item in validated_inputs if item.get("audio_format")),
        None,
    )

    await _create_and_dispatch_demo_job(
        job_id=job_id,
        input_type=mapped_input_type,
        raw_bytes=validated_inputs[0]["raw_bytes"] if is_single_source else b"",
        raw_inputs=validated_inputs,
        source_documents=source_docs,
        raw_text="",
        file_type=(
            validated_inputs[0]["file_type"]
            if is_single_source
            else dispatched_file_type
        ),
        audio_format=(
            validated_inputs[0]["audio_format"] if is_single_source else found_audio_fmt
        ),
        metadata=parsed_metadata,
        background_tasks=background_tasks,
        request=request,
    )
    return {"job_id": job_id, "status": "QUEUED"}


# ---------------------------------------------------------------------------
# /process-json — direct text submission (demo/dev-compatible)
# ---------------------------------------------------------------------------


@app.post("/process-json", status_code=202)
async def process_json(
    request: Request, body: ProcessRequest, background_tasks: BackgroundTasks
):
    """Direct JSON endpoint aligned with the backend API contract."""
    if not body.content or not body.content.strip():
        raise HTTPException(
            status_code=400,
            detail="content is required and cannot be empty or whitespace-only",
        )
    if not isinstance(body.metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")

    if body.job_id:
        try:
            job_id = sanitize_job_id(body.job_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid job_id: {exc}"
            ) from None
    else:
        job_id = f"text_{uuid.uuid4().hex}"

    await _create_and_dispatch_demo_job(
        job_id=job_id,
        input_type="text",
        raw_bytes=b"",
        raw_inputs=[],
        source_documents=body.source_documents or [],
        raw_text=body.content,
        file_type="text",
        metadata=body.metadata or {},
        background_tasks=background_tasks,
        request=request,
    )
    return {"job_id": job_id, "status": "QUEUED"}


async def _create_and_dispatch_demo_job(
    *,
    job_id: str,
    input_type: str,
    raw_bytes: bytes = b"",
    raw_inputs: Optional[List[Dict[str, Any]]] = None,
    source_documents: Optional[List[Dict[str, Any]]] = None,
    raw_text: str = "",
    file_type: str = "document",
    audio_format: Optional[str] = None,
    metadata: dict = {},
    background_tasks: BackgroundTasks,
    request: Request,
) -> None:
    """Shared demo-endpoint path: durable job + queue dispatch (same as prod)."""
    request_id = getattr(request.state, "request_id", None)
    # Demo submissions always (re)run — reprocess=True so a re-used id re-dispatches.
    await get_or_create_job(
        job_id=job_id,
        input_type=input_type,
        options=JobOptions(),
        reprocess=True,
    )
    initial_state = make_initial_state(
        job_id,
        raw_bytes=raw_bytes,
        raw_inputs=raw_inputs or [],
        source_documents=source_documents or [],
        raw_text=raw_text,
        file_type=file_type,
        audio_format=audio_format,
        metadata=metadata,
    )
    # In the Redis path the worker reconstructs state; base64 bytes / text are
    # cached transiently so the demo endpoints keep working in production too.
    cache_input = {
        "raw_text": raw_text,
        "raw_bytes": raw_bytes,
        "raw_inputs": raw_inputs or [],
        "source_documents": source_documents or [],
        "file_type": file_type,
        "audio_format": audio_format,
        "metadata": metadata,
    }
    await dispatch_job(
        job_id,
        initial_state=initial_state,
        pipeline=pipeline,  # module global — mock-patchable in tests
        background_tasks=background_tasks,
        request_id=request_id,
        cache_input=cache_input,
    )
