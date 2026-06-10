from __future__ import annotations

import io
import json
import logging
import re
from typing import Any, Optional, TypedDict

import docx
import fitz  # PyMuPDF
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.llm import get_llm
from app.schemas.pipeline_state import PipelineState

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 50
RELEVANCE_SNIPPET_CHARS = 2000

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")


class IngestOutput(TypedDict):
    raw_text: Optional[str]
    is_useful: bool
    relevance_score: float
    status: str
    error: Optional[str]


class RelevanceCheck(BaseModel):
    is_useful: bool = Field(
        description=(
            "True only if the document contains software requirements, technical "
            "specifications, or meeting notes relevant to software delivery."
        )
    )
    relevance_score: float = Field(description="Confidence score between 0 and 1.")
    reason: str = Field(description="Short reason explaining acceptance or rejection.")


def _build_output(
    *,
    raw_text: Optional[str],
    is_useful: bool,
    relevance_score: float,
    status: str,
    error: Optional[str],
) -> IngestOutput:
    return {
        "raw_text": raw_text,
        "is_useful": is_useful,
        "relevance_score": max(0.0, min(1.0, float(relevance_score))),
        "status": status,
        "error": error,
    }


def _normalize_text(text: str) -> str:
    """Normalize whitespace for stable downstream parsing and idempotent outputs."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines: list[str] = []
    previous_was_blank = False

    for line in normalized.split("\n"):
        compact = re.sub(r"[ \t]+", " ", line).strip()
        if not compact:
            if not previous_was_blank:
                lines.append("")
            previous_was_blank = True
            continue

        lines.append(compact)
        previous_was_blank = False

    return "\n".join(lines).strip()


def _mask_phone(match: re.Match[str]) -> str:
    candidate = match.group(0)
    digits_only = re.sub(r"\D", "", candidate)
    if 7 <= len(digits_only) <= 15:
        return "[PHONE]"
    return candidate


def _mask_pii(text: str) -> str:
    """Mask lightweight PII fields before downstream LLM processing."""
    masked = EMAIL_PATTERN.sub("[EMAIL]", text)
    masked = PHONE_PATTERN.sub(_mask_phone, masked)
    return masked


def _extract_pdf(raw_bytes: bytes) -> tuple[str, Optional[str]]:
    if not raw_bytes:
        return "", "INGEST_FAILED: missing PDF bytes"

    try:
        pages: list[str] = []
        with fitz.open(stream=raw_bytes, filetype="pdf") as document:
            for page in document:
                # Use \f (form feed) as page separator for parse_to_chunks
                pages.append(page.get_text())
        return "\f".join(pages), None
    except Exception as exc:
        logger.warning("PDF extraction failed: %s", exc)
        return "", f"INGEST_FAILED: PDF extraction error ({exc})"


def _extract_docx(raw_bytes: bytes) -> tuple[str, Optional[str]]:
    if not raw_bytes:
        return "", "INGEST_FAILED: missing DOCX bytes"

    try:
        document = docx.Document(io.BytesIO(raw_bytes))
        # Use \n\n as paragraph separator
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        return "\n\n".join(paragraphs), None
    except Exception as exc:
        logger.warning("DOCX extraction failed: %s", exc)
        return "", f"INGEST_FAILED: DOCX extraction error ({exc})"


def extract_pdf(raw_bytes: bytes) -> str:
    """Backward-compatible PDF extractor API used by existing callers."""
    text, _ = _extract_pdf(raw_bytes)
    return text


def extract_docx(raw_bytes: bytes) -> str:
    """Backward-compatible DOCX extractor API used by existing callers."""
    text, _ = _extract_docx(raw_bytes)
    return text


def _extract_from_state(state: PipelineState, file_type: str) -> tuple[str, Optional[str]]:
    raw_text = state.get("raw_text")
    # If text is already provided, assume it's already sanitized or handle as plain text
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text, None

    raw_bytes = state.get("raw_bytes", b"") or b""

    if file_type == "pdf":
        return _extract_pdf(raw_bytes)

    if file_type == "docx":
        return _extract_docx(raw_bytes)

    if file_type == "text":
        if not raw_bytes:
            return "", None
        try:
            return raw_bytes.decode("utf-8"), None
        except UnicodeDecodeError:
            # Fallback keeps ingest resilient for legacy encodings.
            return raw_bytes.decode("latin-1", errors="replace"), None

    return "", f"INGEST_FAILED: unsupported file_type '{file_type}'"


def _heuristic_relevance(snippet: str) -> RelevanceCheck:
    keywords = [
        "requirement",
        "user story",
        "acceptance criteria",
        "api",
        "backend",
        "frontend",
        "system",
        "functional",
        "meeting",
        "architecture",
        "sprint",
        "task",
    ]
    lowered = snippet.lower()
    hits = sum(1 for term in keywords if term in lowered)
    score = min(1.0, hits / 6.0)
    is_useful = hits >= 2
    reason = (
        "Heuristic fallback accepted the document as software-related."
        if is_useful
        else "Heuristic fallback rejected the document as not software-related."
    )
    return RelevanceCheck(is_useful=is_useful, relevance_score=score, reason=reason)


async def _run_relevance_check(masked_text: str) -> RelevanceCheck:
    # Remove markers before checking relevance to avoid biasing the LLM
    clean_snippet = masked_text.replace('\f', ' ')[:RELEVANCE_SNIPPET_CHARS]

    system_prompt = (
        "You are a strict software-document gatekeeper. Accept only content "
        "related to software requirements, engineering specs, architecture "
        "notes, planning notes, backlog items, or sprint/meeting notes for "
        "software products. Reject spam, personal notes, random lists, "
        "marketing content, and unrelated domains.\n\n"
        "Return ONLY valid JSON. No markdown. No explanations.\n"
        "Shape: {\"is_useful\": bool, \"relevance_score\": float, \"reason\": \"string\"}"
    )

    user_prompt = f"Classify this snippet and return structured output.\nSnippet:\n{clean_snippet}"

    try:
        llm = get_llm()
        raw = await llm.ainvoke([
            ("system", system_prompt),
            ("user", user_prompt)
        ])
        content = getattr(raw, "content", None) or str(raw)
        
        # Strip code fences
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].startswith("```"): lines = lines[:-1]
            content = "\n".join(lines).strip()

        try:
            parsed = json.loads(content)
            response = RelevanceCheck.model_validate(parsed)
            return RelevanceCheck(
                is_useful=bool(response.is_useful),
                relevance_score=max(0.0, min(1.0, float(response.relevance_score))),
                reason=(response.reason or "No reason provided.").strip(),
            )
        except Exception as pe:
            print(f"Ingest relevance parse error: {pe}")
            raise pe

    except Exception as exc:
        logger.warning("LLM relevance check failed, using heuristic fallback: %s", exc)
        fallback = _heuristic_relevance(clean_snippet)
        return RelevanceCheck(
            is_useful=fallback.is_useful,
            relevance_score=fallback.relevance_score,
            reason=f"{fallback.reason} (LLM unavailable)",
        )


async def ingest_node(state: PipelineState) -> IngestOutput:
    """Ingest input, extract/clean text, mask PII, and classify relevance for routing."""
    print("--- INGEST NODE ---")
    # Trust the file_type determined by detect_file_type

    file_type = str(state.get("file_type", "")).strip().lower()

    if file_type == "audio":
        return _build_output(
            raw_text=state.get("raw_text"),
            is_useful=True,
            relevance_score=1.0,
            status="to_transcribe",
            error=None,
        )

    try:
        extracted_text, extraction_error = _extract_from_state(state, file_type)
        if extraction_error:
            return _build_output(
                raw_text=None,
                is_useful=False,
                relevance_score=0.0,
                status="rejected",
                error=extraction_error,
            )

        # Basic normalization (keeping markers)
        normalized_text = extracted_text.strip()
        
        if len(normalized_text) < MIN_TEXT_LENGTH:
            return _build_output(
                raw_text=normalized_text or None,
                is_useful=False,
                relevance_score=0.0,
                status="rejected",
                error=f"INGEST_EMPTY: text too short ({len(normalized_text)} chars)",
            )

        masked_text = _mask_pii(normalized_text)
        if len(masked_text) < MIN_TEXT_LENGTH:
            return _build_output(
                raw_text=masked_text or None,
                is_useful=False,
                relevance_score=0.0,
                status="rejected",
                error=f"INGEST_EMPTY: text too short after masking ({len(masked_text)} chars)",
            )

        relevance = await _run_relevance_check(masked_text[:RELEVANCE_SNIPPET_CHARS])
        if not relevance.is_useful:
            return _build_output(
                raw_text=masked_text,
                is_useful=False,
                relevance_score=relevance.relevance_score,
                status="rejected",
                error=f"DOCUMENT_REJECTED: {relevance.reason}",
            )

        return _build_output(
            raw_text=masked_text,
            is_useful=True,
            relevance_score=relevance.relevance_score,
            status="ready_for_chunking",
            error=None,
        )
    except Exception as exc:
        logger.exception("Unhandled ingest failure")
        return _build_output(
            raw_text=None,
            is_useful=False,
            relevance_score=0.0,
            status="rejected",
            error=f"INGEST_FAILED: {exc}",
        )


def route_after_ingest(state: PipelineState) -> str:
    """Route based on ingest status to transcribe, chunking, or format."""
    status = str(state.get("status", "")).strip().lower()

    if status == "to_transcribe":
        return "transcribe"
    if status == "ready_for_chunking":
        return "parse_to_chunks"
    if status == "rejected":
        return "format"

    # Backward compatibility for legacy states not yet using explicit status.
    if state.get("error"):
        return "format"
    if state.get("file_type") == "audio":
        return "transcribe"
    if state.get("is_useful") is False:
        return "format"
    return "parse_to_chunks"


def build_ingest_entry_graph() -> Any:
    """Build a minimal working graph with ingest as entry point and conditional routing."""
    from langgraph.graph import END, StateGraph

    from app.nodes.extract import extract_node
    from app.nodes.format import format_node
    from app.nodes.transcribe import transcribe_node

    workflow = StateGraph(PipelineState)
    workflow.add_node("ingest", ingest_node)
    workflow.add_node("transcribe", transcribe_node)
    workflow.add_node("extract", extract_node)
    workflow.add_node("format", format_node)

    workflow.set_entry_point("ingest")
    workflow.add_conditional_edges(
        "ingest",
        route_after_ingest,
        {
            "transcribe": "transcribe",
            "extract": "extract",
            "format": "format",
        },
    )

    workflow.add_edge("transcribe", "extract")
    workflow.add_edge("extract", "format")
    workflow.add_edge("format", END)

    return workflow.compile()
