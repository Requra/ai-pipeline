from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.services.source_processing.models import SourceInput, ProcessedSource
from app.services.source_processing.document import process_document_source
from app.services.source_processing.audio import process_audio_source

logger = logging.getLogger(__name__)


async def process_single_source(
    source: SourceInput,
    job_id: str,
    *,
    language: str = "en",
    transcribe_options: Optional[Dict[str, Any]] = None,
    enable_pii: bool = True,
    stt_semaphore: Optional[asyncio.Semaphore] = None,
) -> ProcessedSource:
    """Safely process a single source with error isolation."""
    file_type = (source.file_type or "text").lower()
    try:
        if file_type == "audio":
            return await process_audio_source(
                source,
                job_id,
                language=language,
                transcribe_options=transcribe_options,
                enable_pii=enable_pii,
                stt_semaphore=stt_semaphore,
            )
        else:
            return await process_document_source(
                source,
                job_id,
                language=language,
                enable_pii=enable_pii,
            )
    except Exception as exc:
        logger.error(
            "Unexpected error while processing source %s (%s, type=%s): %s",
            source.document_id,
            source.filename,
            file_type,
            exc,
            exc_info=True,
        )
        return ProcessedSource(
            document_id=source.document_id,
            filename=source.filename,
            source_type=file_type,
            status="failed",
            error_code="SOURCE_PROCESSING_FAILED",
            error_message=f"SOURCE_PROCESSING_FAILED: {str(exc)}",
        )
