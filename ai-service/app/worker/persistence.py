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
    """Persist all source-document rows + all parsed chunks for the job.

    Returns the persisted chunk records (used downstream for embeddings).
    """
    chunks = state.get("chunks") or []
    if not chunks:
        return []

    doc_records: List[SourceDocumentRecord] = []
    doc_map: Dict[str, str] = {}
    default_doc_id: Optional[str] = None

    # Load existing documents for the job to merge/preserve fields
    try:
        existing_docs = await stores.chunks.get_documents(job.job_id)
    except Exception as exc:
        logger.warning("Failed to fetch existing source documents for merge: %s", exc)
        existing_docs = []

    existing_by_backend_id = {
        doc.backend_document_id: doc for doc in existing_docs if doc.backend_document_id
    }

    state_source_docs = state.get("source_documents") or []
    if state_source_docs:
        for idx, doc in enumerate(state_source_docs, start=1):
            d_id = doc.get("document_id") or f"SRC-{str(idx).zfill(3)}"
            existing = existing_by_backend_id.get(d_id)
            if existing:
                doc_rec = SourceDocumentRecord(
                    id=existing.id,
                    job_id=job.job_id,
                    tenant_id=job.tenant_id or existing.tenant_id,
                    project_id=job.project_id or existing.project_id,
                    backend_document_id=d_id,
                    source_type=doc.get("file_type") or existing.source_type or "text",
                    file_name=doc.get("filename") or doc.get("file_name") or existing.file_name or d_id,
                    mime_type=doc.get("mime_type") or existing.mime_type or "application/octet-stream",
                    storage_key=doc.get("storage_key") or existing.storage_key,
                    file_url=doc.get("file_url") or existing.file_url,
                    sha256_hash=doc.get("sha256_hash") or doc.get("hash") or existing.sha256_hash,
                    language=doc.get("language") or existing.language or job.options.language,
                    page_count=doc.get("page_count") or existing.page_count,
                )
            else:
                doc_rec = SourceDocumentRecord(
                    job_id=job.job_id,
                    tenant_id=job.tenant_id,
                    project_id=job.project_id,
                    backend_document_id=d_id,
                    source_type=doc.get("file_type") or "text",
                    file_name=doc.get("filename") or doc.get("file_name") or d_id,
                    mime_type=doc.get("mime_type") or "application/octet-stream",
                    storage_key=doc.get("storage_key"),
                    file_url=doc.get("file_url"),
                    sha256_hash=doc.get("sha256_hash") or doc.get("hash"),
                    language=doc.get("language") or job.options.language,
                    page_count=doc.get("page_count"),
                )
            doc_records.append(doc_rec)
            
        try:
            saved_docs = await stores.chunks.save_documents(doc_records)
            for idx, doc_rec in enumerate(doc_records):
                d_id = state_source_docs[idx].get("document_id") or f"SRC-{str(idx+1).zfill(3)}"
                if idx < len(saved_docs):
                    doc_map[d_id] = saved_docs[idx].id
                    if default_doc_id is None:
                        default_doc_id = saved_docs[idx].id
        except Exception as exc:  # pragma: no cover - store dependent
            logger.warning("persist source documents failed: %s", type(exc).__name__)
    
    if not doc_records:
        if existing_docs:
            doc_records = existing_docs
            # Populate doc_map from existing
            for doc in existing_docs:
                if doc.backend_document_id:
                    doc_map[doc.backend_document_id] = doc.id
                    if default_doc_id is None:
                        default_doc_id = doc.id
        else:
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
            try:
                saved = await stores.chunks.save_documents([doc])
                default_doc_id = saved[0].id if saved else None
            except Exception as exc:  # pragma: no cover - store dependent
                logger.warning("persist source document fallback failed: %s", type(exc).__name__)

    records: List[SourceChunkRecord] = []
    for idx, ch in enumerate(chunks):
        ch_doc_id = getattr(ch, "document_id", None)
        if not ch_doc_id and getattr(ch, "chunk_id", None) and state_source_docs:
            for doc in state_source_docs:
                d_id = doc.get("document_id")
                if d_id and f"_{d_id}_" in ch.chunk_id:
                    ch_doc_id = d_id
                    break

        resolved_doc_id = doc_map.get(ch_doc_id) if ch_doc_id else default_doc_id

        records.append(
            SourceChunkRecord(
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                project_id=job.project_id,
                source_document_id=resolved_doc_id,
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
    except Exception as exc:
        logger.error("persist result failed: %s", type(exc).__name__)
        raise
    return payload
