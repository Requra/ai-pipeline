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
    logger.info("--- DETECT FILE TYPE NODE ---")
    update_progress(state.get("job_id"), "detect_file_type", 5, "PROCESSING")
    
    raw_inputs = state.get("raw_inputs") or []
    if raw_inputs:
        normalized_inputs = []
        source_documents_by_id = {
            doc.get("document_id"): dict(doc)
            for doc in state.get("source_documents", [])
            if doc.get("document_id")
        }
        detected_kinds = set()

        for raw_input in raw_inputs:
            file_bytes = raw_input.get("raw_bytes") or b""
            filename = raw_input.get("filename") or "unknown_file"
            if not file_bytes:
                return {"status": "rejected", "error": f"FILE_TYPE_REJECTED: empty file payload for '{filename}'"}

            file_type, mime_type, subtype = detect_mime_and_type(file_bytes, filename)
            if file_type == "unknown":
                return {
                    "status": "rejected",
                    "error": f"FILE_TYPE_REJECTED: unsupported file format for '{filename}' (signature not recognized)",
                }
            from app.config import settings
            max_size = settings.MAX_AUDIO_BYTES if file_type == "audio" else settings.MAX_DOCUMENT_BYTES
            if len(file_bytes) > max_size:
                return {
                    "status": "rejected",
                    "error": f"FILE_TYPE_REJECTED: file too large for '{filename}'",
                }

            normalized = dict(raw_input)
            normalized.update({"file_type": file_type, "mime_type": mime_type, "audio_format": subtype})
            normalized_inputs.append(normalized)
            detected_kinds.add("audio" if file_type == "audio" else "document")

            document_id = normalized.get("document_id")
            if document_id:
                source_doc = source_documents_by_id.setdefault(document_id, {})
                source_doc.update({
                    "document_id": document_id,
                    "filename": filename,
                    "file_type": file_type,
                    "mime_type": mime_type,
                    "sha256_hash": normalized.get("sha256_hash"),
                })

        audio_count = sum(1 for item in normalized_inputs if item["file_type"] == "audio")
        max_audio = getattr(settings, "MAX_AUDIO_SOURCES_PER_JOB", 1)
        if audio_count > max_audio:
            return {
                "status": "rejected",
                "error": f"FILE_TYPE_REJECTED: multiple audio inputs exceed limit of {max_audio}",
            }

        has_audio = "audio" in detected_kinds
        has_doc = "document" in detected_kinds
        if has_audio and has_doc:
            if not getattr(settings, "ENABLE_MIXED_SOURCE_JOBS", True):
                return {
                    "status": "rejected",
                    "error": "FILE_TYPE_REJECTED: mixed document and audio sources are disabled by ENABLE_MIXED_SOURCE_JOBS",
                }
            overall_file_type = "sources"
        elif has_audio:
            overall_file_type = "audio"
        else:
            overall_file_type = "document"

        audio_fmt = None
        for item in normalized_inputs:
            if item.get("file_type") == "audio":
                audio_fmt = item.get("audio_format")
                break

        first = normalized_inputs[0]
        return {
            "raw_inputs": normalized_inputs,
            "source_documents": list(source_documents_by_id.values()),
            "source_metadata": DocumentSource(
                filename=first.get("filename", "unknown_file"),
                file_size_bytes=len(first["raw_bytes"]),
                mime_type=first["mime_type"],
                sha256_hash=first.get("sha256_hash") or hashlib.sha256(first["raw_bytes"]).hexdigest(),
            ),
            "file_type": overall_file_type,
            "audio_format": audio_fmt,
            "status": "type_detected",
        }

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

