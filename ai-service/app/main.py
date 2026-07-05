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
from typing import Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
        request.method, request.url.path, response.status_code, duration_ms, request_id,
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
    file: UploadFile = File(...),
    metadata: str = Form("{}"),
    file_type: str = Form("document"),
):
    # ---- Size guard (header-reported) ----------------------------------
    if file.size and file.size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    # ---- Content-type guard --------------------------------------------
    content_type = file.content_type.split(";")[0].strip() if file.content_type else ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported media type: '{file.content_type}'. "
                "Allowed types: PDF, DOCX, TXT, MP3, WAV, OGG, M4A, WEBM."
            ),
        )

    # ---- Metadata JSON guard (400, not 500) ----------------------------
    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {exc.msg}") from None
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")

    # ---- Body size guard (real bytes) ----------------------------------
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    parsed_metadata.setdefault("filename", file.filename)

    # ---- Stable job id: uuid4, or a validated caller-supplied one ------
    provided_job_id = parsed_metadata.get("job_id")
    if provided_job_id:
        try:
            job_id = sanitize_job_id(str(provided_job_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid job_id in metadata: {exc}") from None
    else:
        job_id = f"upload_{uuid.uuid4().hex}"

    await _create_and_dispatch_demo_job(
        job_id=job_id,
        input_type="backend_document",
        raw_bytes=file_bytes,
        raw_text="",
        file_type=file_type,
        metadata=parsed_metadata,
        background_tasks=background_tasks,
        request=request,
    )
    return {"job_id": job_id, "status": "QUEUED"}


# ---------------------------------------------------------------------------
# /process-json — direct text submission (demo/dev-compatible)
# ---------------------------------------------------------------------------

@app.post("/process-json", status_code=202)
async def process_json(request: Request, body: ProcessRequest, background_tasks: BackgroundTasks):
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
            raise HTTPException(status_code=400, detail=f"Invalid job_id: {exc}") from None
    else:
        job_id = f"text_{uuid.uuid4().hex}"

    await _create_and_dispatch_demo_job(
        job_id=job_id,
        input_type="text",
        raw_bytes=b"",
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
    raw_bytes: bytes,
    raw_text: str,
    file_type: str,
    metadata: dict,
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
        raw_text=raw_text,
        file_type=file_type,
        metadata=metadata,
    )
    # In the Redis path the worker reconstructs state; base64 bytes / text are
    # cached transiently so the demo endpoints keep working in production too.
    cache_input = {
        "raw_text": raw_text,
        "raw_bytes": raw_bytes,
        "file_type": file_type,
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
