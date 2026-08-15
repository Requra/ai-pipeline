from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.config import settings
from app.services.source_processing.models import SourceInput, ProcessedSource
from app.nodes.ingest import _mask_pii, _run_relevance_check
from app.nodes.transcribe import (
    _validate_ffmpeg,
    _transcribe_groq,
    _transcribe_deepgram,
)
from app.services.audio_semantics import reconstruct_audio_chunks

logger = logging.getLogger(__name__)


async def process_audio_source(
    source: SourceInput,
    job_id: str,
    *,
    language: str = "ar",
    transcribe_options: Optional[Dict[str, Any]] = None,
    enable_pii: bool = True,
    stt_semaphore: Optional[asyncio.Semaphore] = None,
) -> ProcessedSource:
    """Process a single audio source (transcribe, clean, PII mask, relevance check, chunk)."""
    document_id = source.document_id
    filename = source.filename
    raw_bytes = source.raw_bytes
    file_subtype = source.audio_format or "mp3"
    opts = transcribe_options or {}
    allow_dual_run = opts.get("allow_dual_run", True)

    # 1. Validate dependencies & bytes
    try:
        _validate_ffmpeg()
    except RuntimeError as e:
        logger.error("FFmpeg validation failed: %s", e)
        return ProcessedSource(
            document_id=document_id,
            filename=filename,
            source_type="audio",
            status="failed",
            error_code="TRANSCRIBE_SYSTEM_DEPENDENCY_MISSING",
            error_message=str(e),
        )

    if not raw_bytes:
        return ProcessedSource(
            document_id=document_id,
            filename=filename,
            source_type="audio",
            status="failed",
            error_code="TRANSCRIBE_NO_BYTES",
            error_message=f"No audio bytes provided for '{filename}'",
        )

    # 2. STT Provider selection
    provider = settings.TRANSCRIBE_PROVIDER
    primary_fn = _transcribe_groq if provider == "groq" else _transcribe_deepgram
    fallback_fn = _transcribe_deepgram if provider == "groq" else _transcribe_groq
    fallback_provider = "deepgram" if provider == "groq" else "groq"

    async def _invoke_stt(fn, is_dg: bool):
        if stt_semaphore:
            async with stt_semaphore:
                if is_dg:
                    return await fn(raw_bytes, file_subtype, job_id, language, allow_dual_run)
                return await fn(raw_bytes, file_subtype, job_id, language)
        else:
            if is_dg:
                return await fn(raw_bytes, file_subtype, job_id, language, allow_dual_run)
            return await fn(raw_bytes, file_subtype, job_id, language)

    # 3. Call Primary STT with fallback
    raw_transcript: str = ""
    utterances = []
    warning_code: Optional[str] = None
    warning_message: Optional[str] = None

    try:
        logger.info("Calling primary STT provider '%s' for document_id=%s (%s)", provider, document_id, filename)
        raw_transcript, utterances = await _invoke_stt(primary_fn, primary_fn == _transcribe_deepgram)
    except Exception as exc:
        primary_err = f"TRANSCRIBE_{provider.upper()}_FAILURE: {exc}"
        logger.warning("Primary STT provider '%s' failed for document_id=%s: %s; trying fallback '%s'", provider, document_id, exc, fallback_provider)
        try:
            raw_transcript, utterances = await _invoke_stt(fallback_fn, fallback_fn == _transcribe_deepgram)
            warning_code = "STT_FALLBACK_USED"
            warning_message = f"Primary STT provider '{provider}' failed ({primary_err}); successfully used fallback '{fallback_provider}'."
        except Exception as fb_exc:
            fallback_err = f"TRANSCRIBE_FALLBACK_FAILURE: {fb_exc}"
            logger.error("All STT providers failed for document_id=%s: %s | %s", document_id, primary_err, fallback_err)
            return ProcessedSource(
                document_id=document_id,
                filename=filename,
                source_type="audio",
                status="failed",
                error_code="STT_ALL_PROVIDERS_FAILED",
                error_message=f"All STT providers failed: {primary_err} | {fallback_err}",
            )

    # 4. Normalize utterances to SourceChunk list and reconstruct audio chunks
    from app.schemas.items import SourceChunk
    normalized_utterances: list[SourceChunk] = []
    for idx, u in enumerate(utterances, start=1):
        if isinstance(u, SourceChunk):
            u_copy = u.model_copy(update={"document_id": document_id})
            normalized_utterances.append(u_copy)
        elif isinstance(u, dict):
            u_text = u.get("text", "")
            normalized_utterances.append(
                SourceChunk(
                    chunk_id=u.get("chunk_id") or f"{job_id}_aud_{document_id}_{idx}",
                    document_id=document_id,
                    text=u_text,
                    start_char=u.get("start_char", 0),
                    end_char=u.get("end_char", len(u_text)),
                    speaker=u.get("speaker"),
                    start_time_sec=u.get("start_time_sec") if "start_time_sec" in u else u.get("start"),
                    end_time_sec=u.get("end_time_sec") if "end_time_sec" in u else u.get("end"),
                    language=u.get("language") or language,
                    asr_confidence=u.get("asr_confidence") if "asr_confidence" in u else u.get("confidence"),
                )
            )

    chunks = reconstruct_audio_chunks(
        normalized_utterances,
        job_id=job_id,
        document_id=document_id,
        default_language=language,
    )
    full_transcript = "\n\n".join(chunk.text for chunk in chunks)

    # 5. PII Masking on transcript and chunks
    pii_stats = None
    if enable_pii:
        masked_transcript, pii_stats = _mask_pii(full_transcript)
        for chunk in chunks:
            masked_chunk_text, _ = _mask_pii(chunk.text)
            chunk.text = masked_chunk_text
    else:
        masked_transcript = full_transcript

    # 6. Per-source Relevance check
    relevance_res = await _run_relevance_check(masked_transcript)
    if not relevance_res.is_useful:
        logger.info("Audio document %s (%s) rejected as irrelevant: %s", document_id, filename, relevance_res.reason)
        return ProcessedSource(
            document_id=document_id,
            filename=filename,
            source_type="audio",
            status="rejected",
            raw_text=masked_transcript,
            is_useful=False,
            relevance_score=relevance_res.relevance_score,
            pii_stats=pii_stats,
            error_code="SOURCE_REJECTED_IRRELEVANT",
            error_message=relevance_res.reason,
            warning_code=warning_code,
            warning_message=warning_message,
        )

    return ProcessedSource(
        document_id=document_id,
        filename=filename,
        source_type="audio",
        status="ready",
        chunks=chunks,
        raw_text=masked_transcript,
        is_useful=True,
        relevance_score=relevance_res.relevance_score,
        pii_stats=pii_stats,
        warning_code=warning_code,
        warning_message=warning_message,
    )
