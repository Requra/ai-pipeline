from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from app.graph.pipeline import build_pipeline
from app.startup import run_startup_checks
from contextlib import asynccontextmanager
from pydantic import BaseModel
import json
import os
import time
from app.progress import progress_store, update_progress

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    """
    run_startup_checks()
    yield

app = FastAPI(
    title="AI Service Pipeline", 
    description="LangGraph execution microservice",
    lifespan=lifespan
)

# Enable CORS for direct React polling
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """
    Health check endpoint for Docker and monitoring.
    """
    return {"status": "healthy", "service": "ai-pipeline"}

pipeline = build_pipeline()

class ProcessRequest(BaseModel):
    job_id: str
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
    "audio/x-mp3"
}

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
                error="PIPELINE_ERROR: job_result not produced by pipeline"
            )
        else:
            update_progress(
                job_id=job_id,
                node_name="format",
                progress_pct=100,
                status=api_status,
                result=job_result
            )
    except Exception as e:
        import traceback
        err_msg = f"{type(e).__name__}: {str(e)}"
        print(f"Background pipeline failed: {err_msg}")
        traceback.print_exc()
        update_progress(
            job_id=job_id,
            node_name="failed",
            progress_pct=100,
            status="FAILED",
            error=err_msg
        )

@app.get("/status/{job_id}")
async def get_job_status(job_id: str):
    if job_id not in progress_store:
        raise HTTPException(status_code=404, detail="Job not found")
    return progress_store[job_id]

@app.post("/process", status_code=202)
async def process_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    metadata: str = Form("{}"),
    file_type: str = Form("document")
):
    if file.size and file.size > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    content_type = file.content_type.split(";")[0].strip() if file.content_type else ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: '{file.content_type}'. Allowed types: PDF, DOCX, TXT, MP3, WAV, OGG, M4A, WEBM."
        )

    try:
        file_bytes = await file.read()
        if len(file_bytes) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large (max 50MB)")
        parsed_metadata = json.loads(metadata)
        if "filename" not in parsed_metadata:
            parsed_metadata["filename"] = file.filename
        
        job_id = "upload_" + str(hash(file.filename)) + "_" + str(int(time.time()))

        initial_state = {
            "job_id": job_id,
            "raw_bytes": file_bytes,
            "raw_text": "", # Nodes will handle extraction
            "file_type": file_type,
            "metadata": parsed_metadata,

            # Intermediate reducers and collections
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

            # Flow control
            "is_useful": False,
            "relevance_score": 0.0,
            "status": "started",
            "error": None,
            "started_at": time.time(),
            "processing_time_ms": 0,

            # Legacy
            "functional_requirements": []
        }

        # Initialize progress store entry
        update_progress(job_id=job_id, node_name="detect_file_type", progress_pct=0, status="QUEUED")

        # Add background task
        background_tasks.add_task(run_pipeline_in_background, job_id, initial_state)

        return {"job_id": job_id, "status": "QUEUED"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/process-json", status_code=202)
async def process_json(
    request: ProcessRequest,
    background_tasks: BackgroundTasks
):
    """
    Direct JSON endpoint aligned with .NET backend API Contract.
    """
    try:
        job_id = request.job_id

        initial_state = {
            "job_id": job_id,
            "raw_bytes": b"", # No file upload
            "raw_text": request.content,
            "file_type": "text",
            "metadata": request.metadata,

            # Intermediate reducers and collections
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

            # Flow control
            "is_useful": False,
            "relevance_score": 0.0,
            "status": "started",
            "error": None,
            "started_at": time.time(),
            "processing_time_ms": 0,

            # Legacy
            "functional_requirements": []
        }

        # Initialize progress store entry
        update_progress(job_id=job_id, node_name="detect_file_type", progress_pct=0, status="QUEUED")

        # Add background task
        background_tasks.add_task(run_pipeline_in_background, job_id, initial_state)

        return {"job_id": job_id, "status": "QUEUED"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
