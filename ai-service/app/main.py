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
        
        initial_state = {
            "job_id": "upload_" + str(hash(file.filename)),
            "raw_bytes": file_bytes,
            "raw_text": "", # Nodes will handle extraction
            "file_type": file_type,
            "metadata": parsed_metadata,
            "functional_requirements": [],
            "classified_requirements": [],
            "user_stories": [],
            "summary": "",
            "status": "started",
            "error": None
        }

        result_state = await pipeline.ainvoke(initial_state)

        # Truncate internal bytes from output response
        result_state.pop("raw_bytes", None)

        return result_state
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/process-json")
async def process_json(request: ProcessRequest):
    """
    Direct JSON endpoint if you already have the text.
    """
    try:
        initial_state = {
            "job_id": request.job_id,
            "raw_bytes": b"", # No file upload
            "raw_text": request.text,
            "file_type": request.file_type,
            "metadata": request.metadata,
            "functional_requirements": [],
            "classified_requirements": [],
            "user_stories": [],
            "summary": "",
            "status": "started",
            "error": None
        }

        result_state = await pipeline.ainvoke(initial_state)
        result_state.pop("raw_bytes", None)

        return result_state
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
