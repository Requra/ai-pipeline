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
    page: Optional[int] = None,
    document_id: Optional[str] = None
) -> SourceChunk:
    """Helper to create a SourceChunk with metadata."""
    page_str = "None" if page is None else str(page)
    chunk_id = f"chk_{job_id}_{document_id}_p{page_str}_c{index}" if document_id else f"chk_{job_id}_{index}"
    return SourceChunk(
        chunk_id=chunk_id,
        text=text,
        start_char=start_char,
        end_char=start_char + len(text),
        page_number=page,
        document_id=document_id
    )


def _chunk_pdf_pages(job_id: str, raw_text: str, document_id: Optional[str] = None) -> List[SourceChunk]:
    """Split text by form-feed markers indicating pages."""
    pages = raw_text.split('\f')
    chunks = []
    current_pos = 0

    for i, page_text in enumerate(pages):
        clean_page = page_text.strip()
        if not clean_page:
            current_pos += len(page_text) + 1 # +1 for \f
            continue

        chunks.append(_create_chunk(job_id, clean_page, i, current_pos, page=i+1, document_id=document_id))
        current_pos += len(page_text) + 1

    return chunks


def _chunk_text_sliding_window(job_id: str, raw_text: str, document_id: Optional[str] = None) -> List[SourceChunk]:
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

        chunks.append(_create_chunk(job_id, chunk_text, index, start, document_id=document_id))

        index += 1
        start = end - CHUNK_OVERLAP_CHARS

        if start >= text_len - CHUNK_OVERLAP_CHARS:
            break

    return chunks


def _chunk_docx_paragraphs(
    job_id: str,
    paragraphs_data: List[dict],
    document_id: Optional[str] = None
) -> List[SourceChunk]:
    chunks = []
    current_chunk_text = ""
    current_chunk_start = 0
    chunk_idx = 0

    first_para_idx = None
    chunk_heading = None
    chunk_section = None

    for para in paragraphs_data:
        text = para["text"]
        para_idx = para["paragraph_index"]
        heading = para.get("heading")
        section = para.get("section")

        if first_para_idx is None:
            first_para_idx = para_idx

        if heading and chunk_heading is None:
            chunk_heading = heading

        if section and chunk_section is None:
            chunk_section = section

        if current_chunk_text:
            current_chunk_text += "\n\n" + text
        else:
            current_chunk_text = text

        if len(current_chunk_text) >= CHUNK_SIZE_CHARS:
            page_str = "None"
            chunk_id = f"chk_{job_id}_{document_id}_p{page_str}_c{chunk_idx}" if document_id else f"chk_{job_id}_{chunk_idx}"
            chunks.append(SourceChunk(
                chunk_id=chunk_id,
                text=current_chunk_text,
                start_char=current_chunk_start,
                end_char=current_chunk_start + len(current_chunk_text),
                page_number=None,
                document_id=document_id,
                paragraph_index=first_para_idx,
                heading=chunk_heading,
                section=chunk_section,
            ))

            current_chunk_start += len(current_chunk_text) + 2
            current_chunk_text = ""
            first_para_idx = None
            chunk_heading = None
            chunk_section = None
            chunk_idx += 1

    if current_chunk_text:
        page_str = "None"
        chunk_id = f"chk_{job_id}_{document_id}_p{page_str}_c{chunk_idx}" if document_id else f"chk_{job_id}_{chunk_idx}"
        chunks.append(SourceChunk(
            chunk_id=chunk_id,
            text=current_chunk_text,
            start_char=current_chunk_start,
            end_char=current_chunk_start + len(current_chunk_text),
            page_number=None,
            document_id=document_id,
            paragraph_index=first_para_idx,
            heading=chunk_heading,
            section=chunk_section,
        ))

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
    if state.get("chunks"):
        return {"status": "chunks_parsed"}

    source_docs = state.get("source_documents") or []
    has_source_docs_with_text = any(doc.get("text") for doc in source_docs)

    chunks: List[SourceChunk] = []

    try:
        if has_source_docs_with_text:
            chunk_idx = 0
            for doc in source_docs:
                doc_text = doc.get("text") or ""
                doc_id = doc.get("document_id")
                doc_file_type = doc.get("file_type") or "text"

                if doc_file_type == "pdf":
                    doc_chunks = _chunk_pdf_pages(job_id, doc_text, document_id=doc_id)
                elif doc_file_type == "docx":
                    if '\f' in doc_text:
                        doc_chunks = _chunk_pdf_pages(job_id, doc_text, document_id=doc_id)
                    else:
                        docx_paragraphs = doc.get("docx_paragraphs")
                        if docx_paragraphs:
                            doc_chunks = _chunk_docx_paragraphs(job_id, docx_paragraphs, document_id=doc_id)
                        else:
                            doc_chunks = _chunk_text_sliding_window(job_id, doc_text, document_id=doc_id)
                else:
                    doc_chunks = _chunk_text_sliding_window(job_id, doc_text, document_id=doc_id)

                chunks.extend(doc_chunks)
        else:
            # Legacy / Single document fallback
            if not raw_text:
                if file_type == "audio":
                    return {"status": "waiting_for_transcription"}
                return {
                    "status": "error",
                    "error": "CHUNK_FAILED: No text provided for chunking"
                }

            if file_type == "pdf":
                chunks = _chunk_pdf_pages(job_id, raw_text)
            elif file_type == "docx":
                if '\f' in raw_text:
                    chunks = _chunk_pdf_pages(job_id, raw_text)
                else:
                    chunks = _chunk_text_sliding_window(job_id, raw_text)
            else:
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
