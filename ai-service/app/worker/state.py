"""
Initial pipeline-state construction + transient input caching.

Two concerns:
  * ``make_initial_state`` — the single canonical builder of a ``PipelineState``
    dict, shared by the demo endpoints and the worker so the shape never drifts.
  * A small Redis-backed input cache. When jobs are dispatched to a separate
    worker process (production), the transient input (inline text, or base64
    file bytes for the demo multipart path) is cached in Redis with a TTL — Redis
    is used strictly as a cache here, never as the source of truth. The durable
    job/chunk/result data always lives in PostgreSQL.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.store.base import StoreBundle
from app.store.models import AiJobRecord, InputType

logger = logging.getLogger("app.worker.state")

_INPUT_TTL_SECONDS = 6 * 60 * 60  # transient input cache lifetime


def make_initial_state(
    job_id: str,
    *,
    raw_bytes: bytes = b"",
    raw_text: str = "",
    file_type: str = "text",
    metadata: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[str] = None,
    project_id: Optional[str] = None,
    enable_embeddings: bool = False,
    enable_hybrid_retrieval: bool = False,
    source_documents: Optional[List[Dict[str, Any]]] = None,
    audio_format: Optional[str] = "mp3",
    language: str = "en",
    transcribe_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the canonical initial ``PipelineState`` dict."""
    return {
        "job_id": job_id,
        "raw_bytes": raw_bytes,
        "raw_text": raw_text,
        "file_type": file_type,
        "metadata": metadata or {},
        "audio_format": audio_format,
        "language": language,
        "transcribe_options": transcribe_options or {},
        "tenant_id": tenant_id,
        "project_id": project_id,
        "enable_embeddings": enable_embeddings,
        "enable_hybrid_retrieval": enable_hybrid_retrieval,
        "source_metadata": None,
        "source_documents": source_documents or [],
        "chunks": [],
        "source_index_id": None,
        "retrieval_stats": None,
        "pii_stats": None,
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "quality_report": None,
        "job_result": None,
        "is_useful": False,
        "relevance_score": 0.0,
        "status": "started",
        "error": None,
        "started_at": time.time(),
        "processing_time_ms": 0,
        "repair_attempts": 0,
        "resolved_quality_issues": [],
        # Legacy
        "functional_requirements": [],
    }


# ---------------------------------------------------------------------------
# Transient input cache (Redis)
# ---------------------------------------------------------------------------

def _input_key(job_id: str) -> str:
    return f"aijob:input:{job_id}"


def stash_input(
    job_id: str,
    *,
    raw_text: str = "",
    raw_bytes: bytes = b"",
    file_type: str = "text",
    metadata: Optional[Dict[str, Any]] = None,
    source_documents: Optional[List[Dict[str, Any]]] = None,
    audio_format: Optional[str] = None,
    language: Optional[str] = None,
    transcribe_options: Optional[Dict[str, Any]] = None,
) -> None:
    """Cache the transient job input in Redis (best effort)."""
    from app.queue.redis_queue import get_redis_connection

    payload = {
        "raw_text": raw_text or "",
        "raw_bytes_b64": base64.b64encode(raw_bytes).decode("ascii") if raw_bytes else "",
        "file_type": file_type,
        "metadata": metadata or {},
        "source_documents": source_documents or [],
        "audio_format": audio_format,
        "language": language,
        "transcribe_options": transcribe_options or {},
    }
    try:
        conn = get_redis_connection()
        conn.set(_input_key(job_id), json.dumps(payload), ex=_INPUT_TTL_SECONDS)
    except Exception as exc:  # pragma: no cover - infra dependent
        logger.warning("stash_input failed for %s: %s", job_id, type(exc).__name__)


def load_input(job_id: str) -> Optional[Dict[str, Any]]:
    from app.queue.redis_queue import get_redis_connection

    try:
        conn = get_redis_connection()
        raw = conn.get(_input_key(job_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as exc:  # pragma: no cover - infra dependent
        logger.warning("load_input failed for %s: %s", job_id, type(exc).__name__)
        return None


async def build_worker_initial_state(
    job: AiJobRecord,
    stores: StoreBundle,
    *,
    backend_client: Any = None,
) -> Dict[str, Any]:
    """Reconstruct the initial pipeline state for a worker-dispatched job.

    Resolution order:
      1. Redis input cache (inline text / demo bytes stashed at enqueue time).
      2. Durable source references from PostgreSQL / download raw bytes or text.
    """
    from app.clients.backend import (
        BackendDocumentClient,
        SourceUnavailableError,
        SourceIntegrityError,
        SourceTooLargeError,
        SourceSecurityError,
    )
    from app.services.file_inspection import detect_mime_and_type

    cached = load_input(job.job_id)
    raw_text = ""
    raw_bytes = b""
    file_type = "text"
    metadata: Dict[str, Any] = {}
    source_documents: List[Dict[str, Any]] = []
    audio_format = "mp3"
    language = job.options.language or "en"
    transcribe_options: Dict[str, Any] = {}

    if cached:
        raw_text = cached.get("raw_text", "") or ""
        b64 = cached.get("raw_bytes_b64", "")
        raw_bytes = base64.b64decode(b64) if b64 else b""
        file_type = cached.get("file_type", "text")
        metadata = cached.get("metadata", {}) or {}
        source_documents = cached.get("source_documents", []) or []
        audio_format = cached.get("audio_format") or "mp3"
        language = cached.get("language") or language
        transcribe_options = cached.get("transcribe_options") or {}

    # If the transient cache expired or is missing, try to reconstruct from PG
    if not source_documents:
        try:
            db_docs = await stores.chunks.get_documents(job.job_id)
            if db_docs:
                source_documents = [
                    {
                        "document_id": doc.backend_document_id,
                        "file_type": doc.source_type,
                        "mime_type": doc.mime_type,
                        "storage_key": doc.storage_key,
                        "file_url": doc.file_url,
                        "sha256_hash": doc.sha256_hash,
                        "page_count": doc.page_count,
                        "filename": doc.file_name,
                        "language": doc.language,
                    }
                    for doc in db_docs
                ]
        except Exception as e:
            logger.warning("Failed to load source documents from PG: %s", e)

    # Resolve from references
    if not raw_text and not raw_bytes and source_documents:
        if backend_client is None:
            backend_client = BackendDocumentClient()

        if job.input_type in (InputType.BACKEND_DOCUMENT.value, InputType.BACKEND_AUDIO.value):
            bytes_list = []
            for ref in source_documents:
                # Downloader will raise SourceDownloadError subclasses on failure
                b = await backend_client.fetch_document_bytes(ref)
                if b:
                    bytes_list.append(b)
            if bytes_list:
                raw_bytes = bytes_list[0]
                # Re-run file inspection on the downloaded bytes
                det_type, det_mime, det_subtype = detect_mime_and_type(raw_bytes)
                # Confirm type is valid for the job input type
                expected_type = "audio" if job.input_type == InputType.BACKEND_AUDIO.value else "document"
                
                is_type_match = False
                if expected_type == "audio" and det_type == "audio":
                    is_type_match = True
                elif expected_type == "document" and det_type in ("pdf", "docx", "text"):
                    is_type_match = True
                    
                if not is_type_match:
                    raise SourceSecurityError(
                        f"Downloaded content type is invalid for job input type"
                    )
                
                file_type = det_type
                if file_type == "audio":
                    audio_format = det_subtype
        else:
            # backend_transcript or text
            texts = []
            for ref in source_documents:
                text = await backend_client.fetch_document_text(ref)
                if text:
                    texts.append(text)
            if texts:
                raw_text = "\n\n".join(texts)
                file_type = "text" if job.input_type != InputType.BACKEND_AUDIO.value else "transcript"

    # If still no content and this is NOT a reference type, we can fallback to chunks (only for "text" inputs)
    if not raw_text and not raw_bytes:
        if job.input_type == InputType.TEXT.value:
            try:
                chunks = await stores.chunks.get_chunks(job.job_id)
            except Exception:
                chunks = []
            if chunks:
                raw_text = "\n\n".join(c.text for c in chunks if c.text)
                file_type = "text"
        else:
            raise SourceUnavailableError("Original source bytes/text could not be recovered for the job")

    # If we still have no raw_text and no raw_bytes, it's a recovery failure!
    if not raw_text and not raw_bytes:
        raise SourceUnavailableError("Original source input is unavailable")

    return make_initial_state(
        job.job_id,
        raw_bytes=raw_bytes,
        raw_text=raw_text,
        file_type=file_type,
        metadata=metadata,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        enable_embeddings=job.options.enable_embeddings,
        enable_hybrid_retrieval=job.options.enable_hybrid_retrieval,
        source_documents=source_documents,
        audio_format=audio_format,
        language=language,
        transcribe_options=transcribe_options,
    )
