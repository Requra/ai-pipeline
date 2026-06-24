import hashlib
import logging
from typing import Optional, Dict, Any, Literal
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import DocumentSource

logger = logging.getLogger(__name__)

# --- Constants & Limits ---
MAX_DOC_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_AUDIO_SIZE = 50 * 1024 * 1024 # 50 MB

SUPPORTED_TYPES = {
    "pdf": b"%PDF-",
    "docx": b"PK\x03\x04", # Zip signature used by Office files
    "audio_mp3": b"ID3",   # common MP3 start
    "audio_wav": b"RIFF",  # common WAV start
    "audio_ogg": b"OggS",  # common OGG start
}

def _detect_mime_and_type(raw_bytes: bytes, metadata: Dict[str, Any]) -> tuple[str, str]:
    """
    Detect file type and MIME from bytes using magic signatures.
    Returns (file_type, mime_type).
    """
    if raw_bytes.startswith(SUPPORTED_TYPES["pdf"]):
        return "pdf", "application/pdf"
    
    if raw_bytes.startswith(SUPPORTED_TYPES["docx"]):
        # Note: PK\x03\x04 is also used for regular ZIPs, but we'll assume DOCX for now 
        # as per project requirements.
        return "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    if raw_bytes.startswith(SUPPORTED_TYPES["audio_mp3"]):
        return "audio", "audio/mpeg"
    
    if raw_bytes.startswith(SUPPORTED_TYPES["audio_wav"]):
        return "audio", "audio/wav"
    
    if raw_bytes.startswith(SUPPORTED_TYPES["audio_ogg"]):
        return "audio", "audio/ogg"

    # Fallback to text if it looks like UTF-8 or ASCII
    try:
        raw_bytes[:1024].decode("utf-8")
        return "text", "text/plain"
    except UnicodeDecodeError:
        pass

    # Check for MP3 without ID3 tag (can start with sync frames 0xFF 0xFB/0xF3/0xF2)
    if len(raw_bytes) > 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0:
        return "audio", "audio/mpeg"

    return "unknown", "application/octet-stream"

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
    file_type, mime_type = _detect_mime_and_type(raw_bytes, metadata)

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

    return {
        "file_type": file_type,
        "source_metadata": source_metadata,
        "status": "type_detected"
    }
