from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.items import SourceChunk


class SourceInput(BaseModel):
    """Normalized input payload for an individual source."""
    document_id: str
    filename: str = "unknown_file"
    file_type: str = "text"
    mime_type: Optional[str] = None
    sha256_hash: Optional[str] = None
    raw_bytes: bytes = b""
    raw_text: Optional[str] = None
    audio_format: Optional[str] = None
    language: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class ProcessedSource(BaseModel):
    """Outcome of processing a single source (document or audio)."""
    document_id: str
    filename: str
    source_type: str  # "pdf", "docx", "text", "audio"
    status: str  # "ready", "rejected", "failed"
    chunks: List[SourceChunk] = Field(default_factory=list)
    raw_text: Optional[str] = None
    relevance_score: Optional[float] = None
    is_useful: Optional[bool] = None
    pii_stats: Optional[Dict[str, int]] = None
    docx_paragraphs: Optional[List[Dict[str, Any]]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    warning_code: Optional[str] = None
    warning_message: Optional[str] = None
