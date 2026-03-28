from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from typing import Optional
from app.graph.pipeline import build_pipeline
import json

app = FastAPI(title="AI Service Pipeline", description="LangGraph execution microservice")

pipeline = build_pipeline()

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
            "file_bytes": file_bytes,
            "file_type": file_type,
            "metadata": parsed_metadata,
            "raw_transcript": "",
            "extracted_items": [],
            "classifications": [],
            "generated_content": None,
            "summary": "",
            "status": "started",
            "error_log": []
        }

        result_state = pipeline.invoke(initial_state)

        # Truncate file bytes from output response to save bandwidth
        result_state.pop("file_bytes", None)

        return result_state
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
