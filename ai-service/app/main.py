"""
FastAPI surface for the AI pipeline service.

Public contract (kept stable for the direct-demo frontend):

  POST /process         (multipart/form-data)
  POST /process-json    (application/json)
  GET  /status/{job_id}
  GET  /health

Error policy:
  * 400 — caller bug (empty content, invalid metadata JSON, etc.).
  * 404 — unknown job id.
  * 413 — file too large.
  * 415 — unsupported content type.
  * 500 — unexpected server bug. Detail is safe text, never a stack trace.
"""

from __future__ import annotations

import json
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
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.graph.pipeline import build_pipeline
from app.progress import update_progress
from app.services.job_store import default_job_store, sanitize_job_id
from app.startup import build_readiness_report, run_startup_checks


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    run_startup_checks()
    yield


app = FastAPI(
    title="AI Service Pipeline",
    description="LangGraph execution microservice",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS — env-driven, no wildcard+credentials in production.
# ---------------------------------------------------------------------------

def _resolve_cors_origins() -> list[str]:
    """
    Resolve allowed origins from `ALLOWED_ORIGINS` env (comma-separated).

    Behaviour:
      * `ALLOWED_ORIGINS` set → use it verbatim. Wildcard `*` is allowed only
        when `ENV != production` AND credentials are disabled implicitly by
        the middleware (we always pass allow_credentials=True, so a wildcard
        is downgraded to the default dev list in production for safety).
      * Unset and `ENV` is anything but `production` → default to a sensible
        local-dev list covering the common Vite / CRA / Live Server ports on
        both `localhost` and `127.0.0.1`.
      * Unset and `ENV=production` → returns an empty list, which the
        middleware treats as "no origin is allowed". Operators MUST set
        `ALLOWED_ORIGINS` explicitly to enable browser callers in prod.
    """
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
    env = os.environ.get("ENV", "development").strip().lower()
    is_production = env in {"production", "prod"}

    if raw:
        items = [item.strip() for item in raw.split(",") if item.strip()]
        # Wildcard with credentials is browser-rejected; downgrade in prod.
        if is_production and "*" in items:
            return _default_dev_origins()
        return items

    if is_production:
        # No explicit origins in production = no CORS opens — operators
        # opt in deliberately. Browser direct calls will be blocked.
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


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker and monitoring."""
    return {"status": "healthy", "service": "ai-pipeline"}


@app.get("/ready")
async def readiness_check():
    """Readiness probe with safe diagnostics (no secrets ever leave this).

    Returns 200 when the configured LLM provider is usable, otherwise 503 so an
    orchestrator can gate traffic. The body reports provider names and boolean
    key-presence flags only — never the key material itself.
    """
    report = build_readiness_report()
    status_code = 200 if report.get("ready") else 503
    return JSONResponse(status_code=status_code, content=report)


# ---------------------------------------------------------------------------
# Pipeline + request models
# ---------------------------------------------------------------------------

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
# Background runner
# ---------------------------------------------------------------------------

async def run_pipeline_in_background(job_id: str, initial_state: dict):
    """Run LangGraph pipeline in the background and update global progress store."""
    try:
        result_state = await pipeline.ainvoke(initial_state)

        status = result_state.get("status", "success")
        api_status = "COMPLETED"
        if status == "error":
            api_status = "FAILED"
        elif status == "partial":
            api_status = "COMPLETED"  # Contract uses completed/failed

        job_result = result_state.get("job_result")
        if not job_result:
            update_progress(
                job_id=job_id,
                node_name="format",
                progress_pct=100,
                status="FAILED",
                error="PIPELINE_ERROR: job_result not produced by pipeline",
            )
        else:
            update_progress(
                job_id=job_id,
                node_name="format",
                progress_pct=100,
                status=api_status,
                result=job_result,
            )
    except Exception as e:  # pragma: no cover — defensive
        import traceback

        err_msg = f"{type(e).__name__}: {str(e)}"
        # Keep the noisy stack trace in server logs only — clients only see
        # the high-level error string via /status.
        print(f"Background pipeline failed: {err_msg}")
        traceback.print_exc()
        update_progress(
            job_id=job_id,
            node_name="failed",
            progress_pct=100,
            status="FAILED",
            error=err_msg,
        )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/status/{job_id}")
async def get_job_status(job_id: str):
    entry = default_job_store.get(job_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Always return the stable contract shape — never leak future internals.
    return {
        "job_id": entry.get("job_id", job_id),
        "status": entry.get("status", "QUEUED"),
        "progress_pct": int(entry.get("progress_pct", 0) or 0),
        "current_node": entry.get("current_node", "started"),
        "result": entry.get("result"),
        "error": entry.get("error"),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
        "completed_at": entry.get("completed_at"),
    }


# ---------------------------------------------------------------------------
# /process — multipart file upload
# ---------------------------------------------------------------------------

def _build_initial_state(
    job_id: str,
    *,
    raw_bytes: bytes,
    raw_text: str,
    file_type: str,
    metadata: dict,
) -> dict:
    return {
        "job_id": job_id,
        "raw_bytes": raw_bytes,
        "raw_text": raw_text,
        "file_type": file_type,
        "metadata": metadata,
        "source_metadata": None,
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "is_useful": False,
        "relevance_score": 0.0,
        "status": "started",
        "error": None,
        "started_at": time.time(),
        "processing_time_ms": 0,
        # Legacy
        "functional_requirements": [],
    }


@app.post("/process", status_code=202)
async def process_document(
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
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metadata JSON: {exc.msg}",
        ) from None
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(
            status_code=400,
            detail="metadata must be a JSON object",
        )

    # ---- Body size guard (real bytes) ----------------------------------
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    parsed_metadata.setdefault("filename", file.filename)

    # ---- Stable job id: uuid4, not hash(filename) ----------------------
    # Honour a backend-provided id (in metadata) only when it is safe; else uuid4.
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

    initial_state = _build_initial_state(
        job_id,
        raw_bytes=file_bytes,
        raw_text="",
        file_type=file_type,
        metadata=parsed_metadata,
    )

    update_progress(
        job_id=job_id,
        node_name="detect_file_type",
        progress_pct=0,
        status="QUEUED",
    )

    background_tasks.add_task(run_pipeline_in_background, job_id, initial_state)
    return {"job_id": job_id, "status": "QUEUED"}


# ---------------------------------------------------------------------------
# /process-json — direct text submission
# ---------------------------------------------------------------------------

@app.post("/process-json", status_code=202)
async def process_json(
    request: ProcessRequest,
    background_tasks: BackgroundTasks,
):
    """Direct JSON endpoint aligned with .NET backend API Contract."""

    # ---- Content guard (400, not 500) ----------------------------------
    if not request.content or not request.content.strip():
        raise HTTPException(
            status_code=400,
            detail="content is required and cannot be empty or whitespace-only",
        )

    # ---- Metadata guard (Pydantic already enforces dict, this is belt-and-suspenders)
    if not isinstance(request.metadata, dict):
        raise HTTPException(
            status_code=400,
            detail="metadata must be a JSON object",
        )

    # Caller can supply a job_id (back-compat with the .NET integration); if
    # absent we generate a stable uuid4 of our own. A supplied id is validated
    # and rejected with 400 when unsafe (never trusted blindly into a store key).
    if request.job_id:
        try:
            job_id = sanitize_job_id(request.job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid job_id: {exc}") from None
    else:
        job_id = f"text_{uuid.uuid4().hex}"

    initial_state = _build_initial_state(
        job_id,
        raw_bytes=b"",
        raw_text=request.content,
        file_type="text",
        metadata=request.metadata or {},
    )

    update_progress(
        job_id=job_id,
        node_name="detect_file_type",
        progress_pct=0,
        status="QUEUED",
    )

    background_tasks.add_task(run_pipeline_in_background, job_id, initial_state)
    return {"job_id": job_id, "status": "QUEUED"}
