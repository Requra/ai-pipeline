"""
Shared file inspection service for request boundary and node type detection.
Authoritatively validates file signatures, magic bytes, container structures,
and cross-validates against declared extensions/MIME types to prevent spoofing.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
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

KNOWN_BINARY_SIGNATURES = (
    b"PK\x03\x04",      # ZIP container
    b"%PDF-",          # PDF
    b"RIFF",           # RIFF / WAV / AVI
    b"ID3",            # MP3 ID3
    b"OggS",           # Ogg
    b"MZ",             # Windows PE/EXE/DLL
    b"\x7fELF",        # Linux ELF
    b"\x1f\x8b",       # GZIP
    b"\x1a\x45\xdf\xa3",  # Matroska / WebM
    b"\x42\x5a\x68",   # BZIP2
    b"\xfd7zXZ",       # XZ
    b"Rar!\x1a\x07",   # RAR
)


def is_valid_pdf(raw_bytes: bytes) -> bool:
    """Validate true PDF structure and header. Reject arbitrary text or binary."""
    if len(raw_bytes) < 5:
        return False
    # PDF must start with %PDF- or contain %PDF- in the initial header block
    if not (raw_bytes.startswith(b"%PDF-") or (b"%PDF-" in raw_bytes[:1024])):
        return False
    # Reject if it starts with other binary headers like MZ, PK, RIFF, ID3, OggS
    if raw_bytes.startswith((b"MZ", b"PK\x03\x04", b"RIFF", b"ID3", b"OggS", b"\x7fELF", b"\x1a\x45\xdf\xa3")):
        return False
    return True


def is_valid_docx(raw_bytes: bytes) -> bool:
    """Validate expected DOCX OOXML ZIP members and guard against ZIP bombs."""
    if not raw_bytes.startswith(SUPPORTED_TYPES["docx"]):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            infolist = z.infolist()
            # Guard against excessive member counts
            if len(infolist) > 500:
                return False

            total_uncompressed = sum(info.file_size for info in infolist)
            # Guard against excessive total uncompressed size (> 50 MB)
            if total_uncompressed > 50 * 1024 * 1024:
                return False

            # Guard against extreme decompression ratio (> 100:1)
            compressed_size = max(len(raw_bytes), 1)
            if total_uncompressed / compressed_size > 100.0 and total_uncompressed > 1024 * 1024:
                return False

            namelist = [info.filename for info in infolist]
            return "[Content_Types].xml" in namelist and any(
                "word/document.xml" in name or name.startswith("word/") for name in namelist
            )
    except Exception:
        return False


def is_valid_mp3(raw_bytes: bytes) -> bool:
    """Validate MP3 by ID3 header or valid MPEG sync frame."""
    if len(raw_bytes) < 4:
        return False
    if raw_bytes.startswith(SUPPORTED_TYPES["audio_mp3"]):
        return True
    # Frame sync: 11 bits set (0xFF followed by high 3 bits set in 2nd byte)
    return len(raw_bytes) > 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0


def is_valid_wav(raw_bytes: bytes) -> bool:
    """Validate WAV: RIFF container with WAVE format marker."""
    return len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WAVE"


def is_valid_ogg(raw_bytes: bytes) -> bool:
    """Validate Ogg container header."""
    return raw_bytes.startswith(SUPPORTED_TYPES["audio_ogg"])


def is_valid_m4a(raw_bytes: bytes) -> bool:
    """Validate the ISO base media ftyp box and supported brands."""
    if len(raw_bytes) < 12:
        return False
    if raw_bytes[4:8] != b"ftyp":
        return False
    major_brand = raw_bytes[8:12]
    return major_brand in (b"M4A ", b"mp42", b"isom", b"dash", b"qt  ")


def is_valid_webm(raw_bytes: bytes) -> bool:
    """Validate supported EBML audio containers used for WebM delivery."""
    if not raw_bytes.startswith(SUPPORTED_TYPES["webm"]):
        return False
    # Cloudinary may deliver an audio-only .webm asset with the compatible
    # Matroska DocType even though the URL and response MIME remain WebM.
    header = raw_bytes[:128].lower()
    return b"webm" in header or b"matroska" in header


def is_valid_text(raw_bytes: bytes) -> bool:
    """Validate plaintext / Markdown without null bytes or binary signatures."""
    if not raw_bytes:
        return False
    for sig in KNOWN_BINARY_SIGNATURES:
        if raw_bytes.startswith(sig):
            return False
    if len(raw_bytes) > 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0:
        return False

    sample = raw_bytes[:4096]
    if b"\x00" in sample:
        return False

    try:
        text_sample = sample.decode("utf-8")
        # Check non-printable ASCII control characters (excluding tab, LF, CR)
        control_chars = sum(1 for c in text_sample if ord(c) < 32 and c not in ("\t", "\n", "\r"))
        if len(text_sample) > 0 and (control_chars / len(text_sample)) > 0.05:
            return False
        return True
    except UnicodeDecodeError:
        return False


def detect_mime_and_type(
    raw_bytes: bytes, filename: Optional[str] = None
) -> Tuple[str, str, str]:
    """Inspect raw bytes and cross-validate against filename extension.

    Returns (file_type, mime_type, subtype).
    file_type: "pdf" | "docx" | "audio" | "text" | "unknown"
    mime_type: formal MIME string
    subtype: specific format ("pdf", "docx", "mp3", "wav", "ogg", "m4a", "webm", "txt", "unknown")
    """
    if not raw_bytes:
        return "unknown", "application/octet-stream", "unknown"

    content_type: str = "unknown"
    mime_type: str = "application/octet-stream"
    subtype: str = "unknown"

    if is_valid_pdf(raw_bytes):
        content_type = "pdf"
        mime_type = "application/pdf"
        subtype = "pdf"
    elif is_valid_docx(raw_bytes):
        content_type = "docx"
        mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        subtype = "docx"
    elif is_valid_mp3(raw_bytes):
        content_type = "audio"
        mime_type = "audio/mpeg"
        subtype = "mp3"
    elif is_valid_wav(raw_bytes):
        content_type = "audio"
        mime_type = "audio/wav"
        subtype = "wav"
    elif is_valid_ogg(raw_bytes):
        content_type = "audio"
        mime_type = "audio/ogg"
        subtype = "ogg"
    elif is_valid_m4a(raw_bytes):
        content_type = "audio"
        mime_type = "audio/x-m4a"
        subtype = "m4a"
    elif is_valid_webm(raw_bytes):
        content_type = "audio"
        mime_type = "audio/webm"
        subtype = "webm"
    elif is_valid_text(raw_bytes):
        content_type = "text"
        mime_type = "text/plain"
        subtype = "txt"

    if content_type == "unknown":
        return "unknown", "application/octet-stream", "unknown"

    # Cross-validate against filename extension if provided
    if filename:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext:
            if ext == "pdf" and content_type != "pdf":
                return "unknown", "application/octet-stream", "unknown"
            if ext == "docx" and content_type != "docx":
                return "unknown", "application/octet-stream", "unknown"
            if ext in ("mp3", "wav", "ogg", "m4a", "webm"):
                if content_type != "audio" or (subtype != ext and not (ext == "mp3" and subtype == "mp3")):
                    return "unknown", "application/octet-stream", "unknown"
            if ext in ("txt", "md", "text", "markdown"):
                if content_type != "text":
                    return "unknown", "application/octet-stream", "unknown"
            if ext in ("exe", "bin", "zip", "tar", "gz", "7z", "rar", "sh", "py", "html", "js", "json", "xml", "csv", "xlsx", "pptx"):
                return "unknown", "application/octet-stream", "unknown"

    return content_type, mime_type, subtype
