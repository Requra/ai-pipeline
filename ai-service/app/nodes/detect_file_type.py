import hashlib
import logging
from typing import Optional, Dict, Any, Literal
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import DocumentSource
from app.services.file_inspection import (
    MAX_DOC_SIZE,
    MAX_AUDIO_SIZE,
    detect_mime_and_type,
)

logger = logging.getLogger(__name__)

from app.progress import update_progress

async def detect_file_type_node(state: PipelineState) -> dict:
    """
    Inspect raw bytes to determine file type and metadata.
    Does not trust state['file_type'] from frontend.
    """
    print("--- DETECT FILE TYPE NODE ---")
    update_progress(state.get("job_id"), "detect_file_type", 5, "PROCESSING")
    
    raw_bytes = state.get("raw_bytes")
    metadata = state.get("metadata", {})
    filename = metadata.get("filename", "unknown_file")

    # Allow text-only inputs: if no raw bytes but raw_text exists, treat as text
    if not raw_bytes:
        raw_text = state.get("raw_text")
        if raw_text:
            file_type = "text"
            mime_type = "text/plain"
            file_size = 0
            sha256_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            source_metadata = DocumentSource(
                filename=filename,
                file_size_bytes=0,
                mime_type=mime_type,
                sha256_hash=sha256_hash
            )

            return {
                "file_type": file_type,
                "source_metadata": source_metadata,
                "status": "type_detected"
            }

        return {
            "status": "rejected",
            "error": "FILE_TYPE_REJECTED: empty file payload"
        }

    file_size = len(raw_bytes)
    file_type, mime_type, subtype = detect_mime_and_type(raw_bytes, filename)

    # Validate file size
    if file_type == "audio":
        if file_size > MAX_AUDIO_SIZE:
            return {
                "status": "rejected",
                "error": f"FILE_TYPE_REJECTED: audio file too large ({file_size / 1024 / 1024:.1f}MB > 50MB)"
            }
    else:
        if file_size > MAX_DOC_SIZE:
            return {
                "status": "rejected",
                "error": f"FILE_TYPE_REJECTED: document too large ({file_size / 1024 / 1024:.1f}MB > 20MB)"
            }

    if file_type == "unknown":
        return {
            "status": "rejected",
            "error": f"FILE_TYPE_REJECTED: unsupported file format (signature not recognized)"
        }

    # Generate SHA256 hash
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    source_metadata = DocumentSource(
        filename=filename,
        file_size_bytes=file_size,
        mime_type=mime_type,
        sha256_hash=sha256_hash
    )

    result = {
        "file_type": file_type,
        "source_metadata": source_metadata,
        "status": "type_detected",
    }
    if file_type == "audio":
        result["audio_format"] = subtype

    return result

