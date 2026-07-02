"""
Persist pipeline artifacts to the durable stores.

Called by the runner as the pipeline progresses (worker/stream path) or once at
the end (in-process path). Everything here is best-effort and defensive: a
persistence hiccup records a warning but never crashes the job, and the
in-memory path silently no-ops against the memory stores.

The AI service persists *derived* data (chunks, embeddings, requirements,
stories, quality, results) — never the original uploaded file bytes, which
remain owned by the backend/object storage.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.store.base import StoreBundle
from app.store.models import (
    AiJobRecord,
    SourceChunkRecord,
    SourceDocumentRecord,
)

logger = logging.getLogger("app.worker.persistence")


def result_to_public(job_result: Any) -> Dict[str, Any]:
    """Coerce a pipeline ``job_result`` (Pydantic model or dict) to JSON dict."""
    if job_result is None:
        return {}
    if hasattr(job_result, "model_dump"):
        return job_result.model_dump(mode="json")
    if isinstance(job_result, dict):
        return job_result
    return {"value": str(job_result)}


async def persist_source_documents_and_chunks(
    stores: StoreBundle, job: AiJobRecord, state: Dict[str, Any]
) -> List[SourceChunkRecord]:
    """Persist a source-document row + all parsed chunks for the job.

    Returns the persisted chunk records (used downstream for embeddings).
    """
    chunks = state.get("chunks") or []
    if not chunks:
        return []

    # One synthetic source-document row capturing the input's metadata. The AI
    # DB stores only the reference/metadata, never the raw file bytes.
    src_meta = state.get("source_metadata")
    file_name = "unknown"
    mime_type = "application/octet-stream"
    if src_meta is not None:
        file_name = getattr(src_meta, "filename", file_name)
        mime_type = getattr(src_meta, "mime_type", mime_type)
    else:
        meta = state.get("metadata") or {}
        file_name = meta.get("filename") or meta.get("file_name") or file_name

    doc = SourceDocumentRecord(
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        source_type=str(state.get("file_type") or "text"),
        file_name=file_name,
        mime_type=mime_type,
        language=job.options.language,
    )
    document_id: Optional[str] = None
    try:
        saved = await stores.chunks.save_documents([doc])
        document_id = saved[0].id if saved else None
    except Exception as exc:  # pragma: no cover - store dependent
        logger.warning("persist source document failed: %s", type(exc).__name__)

    records: List[SourceChunkRecord] = []
    for idx, ch in enumerate(chunks):
        records.append(
            SourceChunkRecord(
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                source_document_id=document_id,
                chunk_id=getattr(ch, "chunk_id", f"chunk-{idx}"),
                chunk_index=idx,
                text=getattr(ch, "text", ""),
                page_number=getattr(ch, "page_number", None),
                speaker=getattr(ch, "speaker", None),
                start_time_sec=getattr(ch, "start_time_sec", None),
                end_time_sec=getattr(ch, "end_time_sec", None),
                start_char=getattr(ch, "start_char", 0) or 0,
                end_char=getattr(ch, "end_char", 0) or 0,
                token_count=len((getattr(ch, "text", "") or "").split()),
            )
        )
    try:
        await stores.chunks.save_chunks(records)
    except Exception as exc:  # pragma: no cover - store dependent
        logger.warning("persist chunks failed: %s", type(exc).__name__)
    return records


async def persist_result(
    stores: StoreBundle,
    job: AiJobRecord,
    job_result: Any,
    *,
    contract_status: str,
    processing_time_ms: int,
) -> Dict[str, Any]:
    """Persist the final JobResult and its decomposed rows. Returns the JSON."""
    payload = result_to_public(job_result)
    # Attach tenant/project so the DB backend can scope decomposed rows.
    if job.tenant_id is not None:
        payload.setdefault("tenant_id", job.tenant_id)
    if job.project_id is not None:
        payload.setdefault("project_id", job.project_id)
    try:
        await stores.results.save_result(
            job.job_id,
            payload,
            contract_version=payload.get("contract_version", "1.0"),
            status=contract_status,
            processing_time_ms=processing_time_ms,
        )
    except Exception as exc:  # pragma: no cover - store dependent
        logger.warning("persist result failed: %s", type(exc).__name__)
    return payload
