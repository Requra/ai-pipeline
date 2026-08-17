from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.progress import update_progress
from app.schemas.items import SourceChunk, DocumentSource
from app.schemas.pipeline_state import PipelineState
from app.services.source_processing import (
    SourceInput,
    ProcessedSource,
    process_single_source,
)

logger = logging.getLogger(__name__)


async def prepare_sources_node(state: PipelineState) -> dict:
    """
    Prepare heterogeneous sources independently (PDF, DOCX, TXT, Audio)
    with bounded concurrency and converge into a single unified chunk corpus.
    """
    logger.info("--- PREPARE SOURCES NODE ---")
    job_id = state.get("job_id") or "unknown"
    update_progress(job_id, "prepare_sources", 20, "PROCESSING")

    raw_inputs = state.get("raw_inputs") or []
    source_documents = state.get("source_documents") or []
    source_documents_by_id = {
        doc.get("document_id"): dict(doc)
        for doc in source_documents
        if doc.get("document_id")
    }

    # 1. Build canonical source inputs list
    source_inputs: List[SourceInput] = []

    if raw_inputs:
        for idx, raw_input in enumerate(raw_inputs, start=1):
            doc_id = raw_input.get("document_id") or f"doc_{idx}"
            source_doc = source_documents_by_id.get(doc_id, {})
            filename = raw_input.get("filename") or source_doc.get("filename") or f"upload_{idx}"
            file_type = raw_input.get("file_type") or source_doc.get("file_type") or "text"
            mime_type = raw_input.get("mime_type") or source_doc.get("mime_type")
            sha256_hash = raw_input.get("sha256_hash") or source_doc.get("sha256_hash")
            raw_bytes = raw_input.get("raw_bytes") or b""
            audio_format = raw_input.get("audio_format") or source_doc.get("audio_format")

            source_inputs.append(
                SourceInput(
                    document_id=doc_id,
                    filename=filename,
                    file_type=file_type,
                    mime_type=mime_type,
                    sha256_hash=sha256_hash,
                    raw_bytes=raw_bytes,
                    raw_text=raw_input.get("raw_text"),
                    audio_format=audio_format,
                    language=state.get("language"),
                )
            )
    else:
        # Fallback for single-file/legacy state payloads
        raw_bytes = state.get("raw_bytes") or b""
        raw_text = state.get("raw_text") or ""
        file_type = state.get("file_type") or "text"
        meta = state.get("metadata") or {}
        first_doc = source_documents[0] if source_documents else {}
        doc_id = first_doc.get("document_id") or "doc_001"
        filename = meta.get("filename") or first_doc.get("filename") or "source_input"
        audio_format = state.get("audio_format") or first_doc.get("audio_format")

        if raw_bytes or raw_text:
            source_inputs.append(
                SourceInput(
                    document_id=doc_id,
                    filename=filename,
                    file_type=file_type,
                    mime_type=first_doc.get("mime_type"),
                    sha256_hash=first_doc.get("sha256_hash"),
                    raw_bytes=raw_bytes,
                    raw_text=raw_text if raw_text else None,
                    audio_format=audio_format,
                    language=state.get("language"),
                )
            )

    if not source_inputs:
        logger.error("No source inputs available for job %s", job_id)
        return {
            "status": "failed",
            "is_useful": False,
            "error": "NO_SOURCES: No valid sources available to process.",
            "chunks": [],
        }

    # 2. Bounded concurrency setup
    source_concurrency = max(1, int(getattr(settings, "SOURCE_PROCESS_CONCURRENCY", 3)))
    stt_concurrency = max(1, int(getattr(settings, "STT_CONCURRENCY", 2)))
    source_sem = asyncio.Semaphore(source_concurrency)
    stt_sem = asyncio.Semaphore(stt_concurrency)
    enable_pii = getattr(settings, "ENABLE_PII_MASKING", True)

    async def _process_bounded(src: SourceInput) -> ProcessedSource:
        async with source_sem:
            return await process_single_source(
                src,
                job_id,
                language=state.get("language", "en"),
                transcribe_options=state.get("transcribe_options", {}),
                enable_pii=enable_pii,
                stt_semaphore=stt_sem,
            )

    # 3. Process all sources concurrently within bounds
    results: List[ProcessedSource] = await asyncio.gather(
        *(_process_bounded(src) for src in source_inputs),
        return_exceptions=False,
    )

    # 4. Merge results & track source-level outcomes
    all_chunks: List[SourceChunk] = []
    ready_sources: List[ProcessedSource] = []
    rejected_sources: List[ProcessedSource] = []
    failed_sources: List[ProcessedSource] = []
    merged_pii_stats: Dict[str, int] = {}
    warnings: List[Dict[str, Any]] = list(state.get("warnings", []) or [])
    processed_sources_payload: List[Dict[str, Any]] = []

    for res in results:
        processed_sources_payload.append(res.model_dump(mode="json"))

        # Update source document manifest entry
        doc_entry = source_documents_by_id.setdefault(res.document_id, {})
        doc_entry.update({
            "document_id": res.document_id,
            "filename": res.filename,
            "file_type": res.source_type,
            "status": res.status,
            "text": res.raw_text,
            "docx_paragraphs": res.docx_paragraphs,
        })

        if res.pii_stats:
            for k, v in res.pii_stats.items():
                merged_pii_stats[k] = merged_pii_stats.get(k, 0) + v

        if res.warning_code:
            warnings.append({
                "node_name": "prepare_sources",
                "code": res.warning_code,
                "message": res.warning_message or "",
            })

        if res.status == "ready":
            ready_sources.append(res)
            all_chunks.extend(res.chunks)
        elif res.status == "rejected":
            rejected_sources.append(res)
            warnings.append({
                "node_name": "prepare_sources",
                "code": "SOURCE_REJECTED_IRRELEVANT",
                "message": f"Source '{res.filename}' ({res.document_id}) was rejected as irrelevant: {res.error_message}",
            })
        else:  # failed
            failed_sources.append(res)
            warnings.append({
                "node_name": "prepare_sources",
                "code": "SOURCE_PROCESSING_FAILED",
                "message": f"Source '{res.filename}' ({res.document_id}) failed processing: {res.error_message}",
            })

    # 5. Determine overall pipeline outcome & relevance
    if len(ready_sources) == 0:
        if len(failed_sources) > 0:
            # No usable sources and at least one technical processing failure -> FAILED
            err_msg = f"ALL_SOURCES_FAILED: No usable sources available ({len(failed_sources)} failed, {len(rejected_sources)} rejected)."
            logger.error("Job %s failed: %s", job_id, err_msg)
            return {
                "status": "failed",
                "is_useful": False,
                "error": err_msg,
                "chunks": [],
                "warnings": warnings,
                "source_documents": list(source_documents_by_id.values()),
                "processed_sources": processed_sources_payload,
            }
        else:
            # All sources were evaluated and explicitly rejected as irrelevant -> REJECTED
            err_msg = "DOCUMENT_REJECTED: All source(s) rejected as irrelevant."
            logger.info("Job %s rejected: %s", job_id, err_msg)
            return {
                "status": "rejected",
                "is_useful": False,
                "error": err_msg,
                "chunks": [],
                "warnings": warnings,
                "source_documents": list(source_documents_by_id.values()),
                "processed_sources": processed_sources_payload,
            }

    # At least one source is ready!
    avg_relevance = sum(s.relevance_score or 0.0 for s in ready_sources) / len(ready_sources)
    partial_failure = len(failed_sources) > 0

    if partial_failure:
        warnings.append({
            "node_name": "prepare_sources",
            "code": "PARTIAL_SOURCE_FAILURE",
            "message": f"{len(failed_sources)} of {len(results)} sources failed; continuing with {len(ready_sources)} usable source(s).",
        })

    # Combined raw text for downstream inspection/summary
    combined_raw_text = "\n\n".join(
        f"--- Source: {s.filename} ({s.document_id}) ---\n{s.raw_text}"
        for s in ready_sources
        if s.raw_text
    )

    stats = {
        "total_sources": len(results),
        "ready_sources": len(ready_sources),
        "rejected_sources": len(rejected_sources),
        "failed_sources": len(failed_sources),
        "total_chunks": len(all_chunks),
    }

    return {
        "chunks": all_chunks,
        "raw_text": combined_raw_text,
        "is_useful": True,
        "relevance_score": avg_relevance,
        "status": "sources_prepared",
        "error": None,
        "warnings": warnings,
        "pii_stats": merged_pii_stats if merged_pii_stats else None,
        "source_documents": list(source_documents_by_id.values()),
        "processed_sources": processed_sources_payload,
        "source_processing_stats": stats,
        "partial_source_failure": partial_failure,
    }
