import logging
import uuid
from typing import List, Optional
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import SourceChunk

logger = logging.getLogger(__name__)

# --- Constants ---
# Target chunk size for text-based splitting
CHUNK_SIZE_CHARS = 3000  # Approx 500-700 tokens
CHUNK_OVERLAP_CHARS = 500

def _create_chunk(
    job_id: str, 
    text: str, 
    index: int, 
    start_char: int, 
    page: Optional[int] = None
) -> SourceChunk:
    """Helper to create a SourceChunk with metadata."""
    return SourceChunk(
        chunk_id=f"chk_{job_id}_{index}",
        text=text,
        start_char=start_char,
        end_char=start_char + len(text),
        page_number=page
    )

def _chunk_pdf_pages(job_id: str, raw_text: str) -> List[SourceChunk]:
    """Split text by form-feed markers indicating pages."""
    pages = raw_text.split('\f')
    chunks = []
    current_pos = 0
    
    for i, page_text in enumerate(pages):
        clean_page = page_text.strip()
        if not clean_page:
            current_pos += len(page_text) + 1 # +1 for \f
            continue
            
        chunks.append(_create_chunk(job_id, clean_page, i, current_pos, page=i+1))
        current_pos += len(page_text) + 1
        
    return chunks

def _chunk_text_sliding_window(job_id: str, raw_text: str) -> List[SourceChunk]:
    """Split text into overlapping chunks of fixed character length."""
    chunks = []
    if not raw_text:
        return []
        
    start = 0
    index = 0
    text_len = len(raw_text)
    
    while start < text_len:
        end = start + CHUNK_SIZE_CHARS
        chunk_text = raw_text[start:end]
        
        # Don't cut in the middle of a word if possible
        if end < text_len:
            last_space = chunk_text.rfind(' ')
            if last_space > CHUNK_SIZE_CHARS * 0.8: # Only backtrack if we don't lose too much
                end = start + last_space
                chunk_text = raw_text[start:end]
        
        chunks.append(_create_chunk(job_id, chunk_text, index, start))
        
        index += 1
        start = end - CHUNK_OVERLAP_CHARS
        
        if start >= text_len - CHUNK_OVERLAP_CHARS:
            break
            
    return chunks

from app.progress import update_progress

async def parse_to_chunks_node(state: PipelineState) -> dict:
    """
    Standardize document parsing into coordinate-aware chunks.
    """
    print("--- PARSE TO CHUNKS NODE ---")
    update_progress(state.get("job_id"), "parse_to_chunks", 25, "PROCESSING")
    
    job_id = state.get("job_id", "unknown")
    file_type = state.get("file_type")
    raw_text = state.get("raw_text")
    
    # If chunks already exist (e.g. from transcription), pass them through
    # Phase 4 will handle transcribing directly to chunks.
    if state.get("chunks"):
        return {"status": "chunks_parsed"}

    if not raw_text:
        # If it's audio, we don't have text yet (Phase 4 will handle this)
        if file_type == "audio":
            return {"status": "waiting_for_transcription"}
        return {
            "status": "error",
            "error": "CHUNK_FAILED: No text provided for chunking"
        }

    chunks: List[SourceChunk] = []
    
    try:
        if file_type == "pdf":
            chunks = _chunk_pdf_pages(job_id, raw_text)
        elif file_type == "docx":
            # For now DOCX is treated as text, but separated by paragraphs in ingest.
            # We use sliding window for stability until Phase 5.
            chunks = _chunk_text_sliding_window(job_id, raw_text)
        else:
            # text or fallback
            chunks = _chunk_text_sliding_window(job_id, raw_text)

        if not chunks and raw_text:
            chunks = [_create_chunk(job_id, raw_text, 0, 0)]

        return {
            "chunks": chunks,
            "status": "chunks_parsed"
        }
        
    except Exception as e:
        logger.exception("Chunking failed")
        return {
            "status": "error",
            "error": f"CHUNK_FAILED: {str(e)}"
        }
