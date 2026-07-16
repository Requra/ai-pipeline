"""
Shared file inspection service for request boundary and node type detection.
"""

from __future__ import annotations

import io
import zipfile
from typing import Dict, Optional, Tuple

MAX_DOC_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_AUDIO_SIZE = 50 * 1024 * 1024  # 50 MB

SUPPORTED_TYPES: Dict[str, bytes] = {
    "pdf": b"%PDF-",
    "docx": b"PK\x03\x04",
    "audio_mp3": b"ID3",
    "audio_wav": b"RIFF",
    "audio_ogg": b"OggS",
    "webm": b"\x1a\x45\xdf\xa3",
}


def is_valid_docx(raw_bytes: bytes) -> bool:
    """Validate expected DOCX ZIP members. Reject generic or malformed ZIPs."""
    if not raw_bytes.startswith(SUPPORTED_TYPES["docx"]):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            namelist = z.namelist()
            return "[Content_Types].xml" in namelist and any(
                "word/document.xml" in name or name.startswith("word/") for name in namelist
            )
    except Exception:
        return False


def is_valid_m4a(raw_bytes: bytes) -> bool:
    """Validate the ISO base media ftyp box and supported brands."""
    if len(raw_bytes) < 12:
        return False
    if raw_bytes[4:8] != b"ftyp":
        return False
    major_brand = raw_bytes[8:12]
    return major_brand in (b"M4A ", b"mp42", b"isom", b"dash", b"qt  ")


def is_valid_webm(raw_bytes: bytes) -> bool:
    """Validate WebM using EBML ID and webm DocType header check."""
    if not raw_bytes.startswith(SUPPORTED_TYPES["webm"]):
        return False
    # Look for "webm" signature in the EBML DocType block (usually first 100 bytes)
    return b"webm" in raw_bytes[:100]


def detect_mime_and_type(
    raw_bytes: bytes, filename: Optional[str] = None
) -> Tuple[str, str, str]:
    """Inspect raw bytes and return (file_type, mime_type, subtype).

    file_type: "pdf" | "docx" | "audio" | "text" | "unknown"
    mime_type: formal MIME string
    subtype: specific format/extension ("pdf", "docx", "mp3", "wav", "ogg", "m4a", "webm", "txt", "unknown")
    """
    if raw_bytes.startswith(SUPPORTED_TYPES["pdf"]):
        return "pdf", "application/pdf", "pdf"

    if is_valid_docx(raw_bytes):
        return "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"

    if raw_bytes.startswith(SUPPORTED_TYPES["audio_mp3"]):
        return "audio", "audio/mpeg", "mp3"

    if raw_bytes.startswith(SUPPORTED_TYPES["audio_wav"]):
        return "audio", "audio/wav", "wav"

    if raw_bytes.startswith(SUPPORTED_TYPES["audio_ogg"]):
        return "audio", "audio/ogg", "ogg"

    if is_valid_m4a(raw_bytes):
        return "audio", "audio/x-m4a", "m4a"

    if is_valid_webm(raw_bytes):
        return "audio", "audio/webm", "webm"

    # Check for MP3 without ID3 tag (starts with sync frames 0xFF 0xFB/0xF3/0xF2)
    if len(raw_bytes) > 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0:
        return "audio", "audio/mpeg", "mp3"

    # Fallback to text if it looks like UTF-8 or ASCII
    try:
        raw_bytes[:1024].decode("utf-8")
        return "text", "text/plain", "txt"
    except UnicodeDecodeError:
        pass

    return "unknown", "application/octet-stream", "unknown"
