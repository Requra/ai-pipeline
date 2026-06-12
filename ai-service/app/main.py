from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body
from typing import Optional
from app.graph.pipeline import build_pipeline
from app.startup import run_startup_checks
from contextlib import asynccontextmanager
from pydantic import BaseModel
import json
import os

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

@app.get("/health")
async def health_check():
    """
    Health check endpoint for Docker and monitoring.
    """
    return {"status": "healthy", "service": "ai-pipeline"}

pipeline = build_pipeline()

class ProcessRequest(BaseModel):
    job_id: str
    text: str
    file_type: str = "pdf"
    metadata: dict = {}

@app.post("/process")
async def process_document(
    file: UploadFile = File(...),
    metadata: str = Form("{}"),
    file_type: str = Form("document")
):
    try:
        file_bytes = await file.read()
        parsed_metadata = json.loads(metadata)
        if "filename" not in parsed_metadata:
            parsed_metadata["filename"] = file.filename
        
        import time

        initial_state = {
            "job_id": "upload_" + str(hash(file.filename)),
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

        result_state = await pipeline.ainvoke(initial_state)

        # Return strict final contract only
        job_result = result_state.get("job_result")
        if not job_result:
            raise HTTPException(status_code=500, detail="PIPELINE_ERROR: job_result not produced by pipeline")

        return job_result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/process-json")
async def process_json(request: ProcessRequest):
    """
    Direct JSON endpoint if you already have the text.
    """
    try:
        import time

        initial_state = {
            "job_id": request.job_id,
            "raw_bytes": b"", # No file upload
            "raw_text": request.text,
            "file_type": request.file_type,
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

        result_state = await pipeline.ainvoke(initial_state)

        job_result = result_state.get("job_result")
        if not job_result:
            raise HTTPException(status_code=500, detail="PIPELINE_ERROR: job_result not produced by pipeline")

        return job_result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
