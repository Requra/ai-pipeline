from __future__ import annotations

import logging
from typing import Optional

from app.services.source_processing.models import SourceInput, ProcessedSource
from app.services.source_processing.extractors import (
    _normalize_text,
    _mask_pii,
    _extract_pdf,
    _extract_docx,
    _run_relevance_check,
    MIN_TEXT_LENGTH,
)
from app.nodes.parse_to_chunks import (
    _create_chunk,
    _chunk_pdf_pages,
    _chunk_docx_paragraphs,
    _chunk_text_sliding_window,
)

logger = logging.getLogger(__name__)


async def process_document_source(
    source: SourceInput,
    job_id: str,
    *,
    language: str = "en",
    enable_pii: bool = True,
) -> ProcessedSource:
    """Process a single document source (PDF, DOCX, TXT/Markdown)."""
    document_id = source.document_id
    filename = source.filename
    file_type = (source.file_type or "text").lower()
    raw_bytes = source.raw_bytes or b""
    filename_lower = (filename or "").lower()
    subtype = (getattr(source, "audio_format", None) or getattr(source, "file_subtype", None) or "").lower()
    mime_type = (source.mime_type or "").lower()

    # Accurate modality detection across file_type, subtype, filename, MIME, and magic signatures
    is_pdf = (
        file_type == "pdf"
        or subtype == "pdf"
        or filename_lower.endswith(".pdf")
        or "pdf" in mime_type
        or raw_bytes.startswith(b"%PDF")
    )
    is_docx = (
        file_type == "docx"
        or subtype == "docx"
        or filename_lower.endswith(".docx")
        or "wordprocessingml" in mime_type
        or "officedocument" in mime_type
        or (raw_bytes.startswith(b"PK\x03\x04") and (filename_lower.endswith(".docx") or "word" in mime_type or not is_pdf))
    )

    detected_source_type = "docx" if is_docx else ("pdf" if is_pdf else "text")

    # 1. Extraction
    extracted_text: str = ""
    extract_error: Optional[str] = None
    paragraphs_data = None

    if source.raw_text:
        extracted_text = source.raw_text
    elif is_pdf:
        extracted_text, extract_error = _extract_pdf(raw_bytes)
    elif is_docx:
        extracted_text, extract_error, paragraphs_data = _extract_docx(raw_bytes)
    elif file_type in ("text", "document", "txt", "markdown", "md") or subtype in ("txt", "text", "md", "markdown"):
        if not raw_bytes:
            extracted_text = ""
        else:
            try:
                extracted_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                extracted_text = raw_bytes.decode("latin-1", errors="replace")
    else:
        # Fallback decode for unrecognized types
        if raw_bytes:
            try:
                extracted_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                extracted_text = raw_bytes.decode("latin-1", errors="replace")
        else:
            extract_error = f"DOCUMENT_EXTRACTION_FAILED: unsupported file_type '{file_type}'"

    if extract_error or not extracted_text.strip():
        err_msg = extract_error or f"DOCUMENT_EXTRACTION_FAILED: empty text extracted from '{filename}'"
        logger.warning("Extraction failed for document %s (%s): %s", document_id, filename, err_msg)
        return ProcessedSource(
            document_id=document_id,
            filename=filename,
            source_type=detected_source_type,
            status="failed",
            error_code="DOCUMENT_EXTRACTION_FAILED",
            error_message=err_msg,
        )

    # 2. Text Normalization
    normalized_text = _normalize_text(extracted_text)

    # 3. PII Masking
    pii_stats = None
    if enable_pii:
        masked_text, pii_stats = _mask_pii(normalized_text)
    else:
        masked_text = normalized_text

    # 4. Relevance Evaluation (Per Source)
    relevance_res = await _run_relevance_check(masked_text)
    if not relevance_res.is_useful:
        logger.info("Document %s (%s) rejected as irrelevant: %s", document_id, filename, relevance_res.reason)
        return ProcessedSource(
            document_id=document_id,
            filename=filename,
            source_type=detected_source_type,
            status="rejected",
            raw_text=masked_text,
            is_useful=False,
            relevance_score=relevance_res.relevance_score,
            pii_stats=pii_stats,
            docx_paragraphs=paragraphs_data,
            error_code="SOURCE_REJECTED_IRRELEVANT",
            error_message=relevance_res.reason,
        )

    # 5. Coordinate-aware Chunking
    chunks = []
    if is_pdf or "\f" in masked_text:
        chunks = _chunk_pdf_pages(job_id, masked_text, document_id=document_id)
    elif is_docx and paragraphs_data:
        # Mask paragraphs text if PII enabled
        if enable_pii:
            masked_paragraphs = []
            for p in paragraphs_data:
                p_text, _ = _mask_pii(p["text"])
                p_copy = dict(p)
                p_copy["text"] = p_text
                masked_paragraphs.append(p_copy)
            chunks = _chunk_docx_paragraphs(job_id, masked_paragraphs, document_id=document_id)
        else:
            chunks = _chunk_docx_paragraphs(job_id, paragraphs_data, document_id=document_id)
    else:
        chunks = _chunk_text_sliding_window(job_id, masked_text, document_id=document_id)

    if not chunks and masked_text:
        chunks = [_create_chunk(job_id, masked_text, 0, 0, document_id=document_id)]

    # Guarantee all chunks carry this document's document_id
    for ch in chunks:
        ch.document_id = document_id

    warning_code = None
    warning_message = None
    if getattr(relevance_res, "decision", "relevant") == "uncertain":
        warning_code = "RELEVANCE_UNCERTAIN_PROCEEDED"
        warning_message = relevance_res.reason

    return ProcessedSource(
        document_id=document_id,
        filename=filename,
        source_type=detected_source_type,
        status="ready",
        raw_text=masked_text,
        is_useful=True,
        relevance_score=relevance_res.relevance_score,
        pii_stats=pii_stats,
        docx_paragraphs=paragraphs_data,
        chunks=chunks,
        warning_code=warning_code,
        warning_message=warning_message,
    )
