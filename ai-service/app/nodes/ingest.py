from __future__ import annotations

import io
import json
import logging
import re
import os
import tempfile
import subprocess
from typing import Any, Optional, TypedDict

import docx
import fitz  # PyMuPDF
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.llm import get_llm
from app.schemas.pipeline_state import PipelineState
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId

from app.config import settings

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 50
RELEVANCE_SNIPPET_CHARS = 2000

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")

# High-confidence Cloud Provider and generic token patterns
OPENAI_KEY_PATTERN = re.compile(r"\b(?:sk-proj-[a-zA-Z0-9_]{32,}|sk-[a-zA-Z0-9]{32,})\b")
AWS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASCA)[A-Z0-9]{16}\b")
GITHUB_KEY_PATTERN = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b")
HF_KEY_PATTERN = re.compile(r"\bhf_[a-zA-Z0-9]{34}\b")
GOOGLE_KEY_PATTERN = re.compile(r"\bAIzaSy[a-zA-Z0-9_\-]{33}\b")
GENERIC_SECRET_PATTERN = re.compile(
    r"(?i)\b(api_key|secret_key|private_key|access_token|db_password)(\s*=\s*['\"]?)([a-zA-Z0-9_\-]{16,})(['\"]?)\b"
)

# Credit Card Candidate Pattern (13 to 16 digits, with optional spaces or hyphens)
CREDIT_CARD_CANDIDATE_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


class IngestOutput(TypedDict):
    raw_text: Optional[str]
    is_useful: bool
    relevance_score: float
    status: str
    error: Optional[str]
    pii_stats: Optional[dict[str, int]]


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
    pii_stats: Optional[dict[str, int]] = None,
) -> IngestOutput:
    return {
        "raw_text": raw_text,
        "is_useful": is_useful,
        "relevance_score": max(0.0, min(1.0, float(relevance_score))),
        "status": status,
        "error": error,
        "pii_stats": pii_stats,
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


def _is_luhn_valid(number: str) -> bool:
    """Implement Luhn checksum algorithm to validate credit card numbers."""
    digits = [int(c) for c in number if c.isdigit()]
    if not digits:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for idx, digit in enumerate(reverse_digits):
        if idx % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return (checksum % 10) == 0


def _mask_phone(match: re.Match[str], stats: dict[str, int]) -> str:
    candidate = match.group(0)
    digits_only = re.sub(r"\D", "", candidate)
    if 7 <= len(digits_only) <= 15:
        stats["phones"] += 1
        return "[PHONE]"
    return candidate


def _mask_pii(text: str) -> tuple[str, dict[str, int]]:
    """Mask lightweight PII and secret fields before downstream LLM processing.

    Returns a tuple of (masked_text, stats_dict).
    """
    stats = {
        "emails": 0,
        "phones": 0,
        "credit_cards": 0,
        "api_keys": 0
    }

    # 1. Emails
    emails = EMAIL_PATTERN.findall(text)
    if emails:
        stats["emails"] = len(emails)
        text = EMAIL_PATTERN.sub("[EMAIL]", text)

    # 2. Phones
    def phone_repl(m):
        return _mask_phone(m, stats)
    text = PHONE_PATTERN.sub(phone_repl, text)

    # 3. Credit Cards with Luhn validation
    def cc_repl(match: re.Match[str]) -> str:
        candidate = match.group(0)
        clean = re.sub(r"\D", "", candidate)
        if _is_luhn_valid(clean):
            stats["credit_cards"] += 1
            return "[CREDIT_CARD]"
        return candidate
    text = CREDIT_CARD_CANDIDATE_PATTERN.sub(cc_repl, text)

    # 4. API Keys (OpenAI, AWS, GitHub, Hugging Face, Google API)
    def api_repl(match: re.Match[str]) -> str:
        stats["api_keys"] += 1
        return "[API_KEY]"

    text = OPENAI_KEY_PATTERN.sub(api_repl, text)
    text = AWS_KEY_PATTERN.sub(api_repl, text)
    text = GITHUB_KEY_PATTERN.sub(api_repl, text)
    text = HF_KEY_PATTERN.sub(api_repl, text)
    text = GOOGLE_KEY_PATTERN.sub(api_repl, text)

    # 5. Generic Secrets
    def generic_repl(match: re.Match[str]) -> str:
        stats["api_keys"] += 1
        key_name = match.group(1)
        delimiter = match.group(2)
        closing_quote = match.group(4)
        return f"{key_name}{delimiter}[API_KEY]{closing_quote}"
    text = GENERIC_SECRET_PATTERN.sub(generic_repl, text)

    return text, stats


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


def convert_docx_to_pdf(docx_bytes: bytes) -> bytes | None:
    """Convert DOCX to PDF using LibreOffice (if installed) or Microsoft Word COM (via PowerShell)."""
    soffice_paths = [
        "C:\\Program Files\\LibreOffice\\program\\soffice.exe",
        "C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe",
    ]
    soffice_bin = None
    for path in soffice_paths:
        if os.path.exists(path):
            soffice_bin = path
            break

    if soffice_bin:
        try:
            with tempfile.TemporaryDirectory() as tempdir:
                in_file = os.path.join(tempdir, "input.docx")
                with open(in_file, "wb") as f:
                    f.write(docx_bytes)

                subprocess.run(
                    [soffice_bin, "--headless", "--convert-to", "pdf", "--outdir", tempdir, in_file],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )

                out_file = os.path.join(tempdir, "input.pdf")
                if os.path.exists(out_file):
                    with open(out_file, "rb") as f:
                        return f.read()
        except Exception as e:
            logger.warning("LibreOffice DOCX to PDF conversion failed: %s", e)

    try:
        with tempfile.TemporaryDirectory() as tempdir:
            in_file = os.path.abspath(os.path.join(tempdir, "input.docx"))
            out_file = os.path.abspath(os.path.join(tempdir, "input.pdf"))

            with open(in_file, "wb") as f:
                f.write(docx_bytes)

            ps_script = f"""
            $word = New-Object -ComObject Word.Application
            $word.Visible = $false
            $doc = $word.Documents.Open('{in_file}')
            $doc.SaveAs('{out_file}', 17)
            $doc.Close()
            $word.Quit()
            """

            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                check=True,
                timeout=30,
            )

            if os.path.exists(out_file):
                with open(out_file, "rb") as f:
                    return f.read()
    except Exception as e:
        logger.warning("Word COM DOCX to PDF conversion failed: %s", e)

    return None


def _extract_docx(raw_bytes: bytes) -> tuple[str, Optional[str]] | tuple[str, Optional[str], Optional[list[dict]]]:
    if not raw_bytes:
        return "", "INGEST_FAILED: missing DOCX bytes", None

    try:
        document = docx.Document(io.BytesIO(raw_bytes))
        paragraphs_data = []
        current_section = None

        for idx, para in enumerate(document.paragraphs):
            text = (para.text or "").strip()
            if not text:
                continue

            style_name = para.style.name if para.style else ""
            is_heading = style_name.startswith("Heading") or style_name.startswith("Title")
            if is_heading:
                current_section = text

            paragraphs_data.append({
                "text": text,
                "paragraph_index": idx,
                "heading": text if is_heading else None,
                "section": current_section,
            })

        full_text = "\n\n".join(p["text"] for p in paragraphs_data)
        return full_text, None, paragraphs_data
    except Exception as exc:
        logger.warning("DOCX extraction failed: %s", exc)
        return "", f"INGEST_FAILED: DOCX extraction error ({exc})", None


def extract_pdf(raw_bytes: bytes) -> str:
    """Backward-compatible PDF extractor API used by existing callers."""
    text, _ = _extract_pdf(raw_bytes)
    return text


def extract_docx(raw_bytes: bytes) -> str:
    """Backward-compatible DOCX extractor API used by existing callers."""
    res = _extract_docx(raw_bytes)
    return res[0]


def _extract_from_state(state: PipelineState, file_type: str) -> tuple[str, Optional[str]]:
    raw_text = state.get("raw_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text, None

    raw_bytes = state.get("raw_bytes", b"") or b""

    if file_type == "pdf":
        return _extract_pdf(raw_bytes)

    if file_type == "docx":
        res = _extract_docx(raw_bytes)
        return res[0], res[1]

    if file_type in ("text", "document"):
        if not raw_bytes:
            return "", None
        try:
            return raw_bytes.decode("utf-8"), None
        except UnicodeDecodeError:
            return raw_bytes.decode("latin-1", errors="replace"), None

    return "", f"INGEST_FAILED: unsupported file_type '{file_type}'"


def _extract_from_bytes(raw_bytes: bytes, file_type: str) -> tuple[str, Optional[str]]:
    """Extract one independently-tracked multipart source."""
    if file_type == "pdf":
        return _extract_pdf(raw_bytes)
    if file_type == "docx":
        res = _extract_docx(raw_bytes)
        return res[0], res[1]
    if file_type in ("text", "document"):
        try:
            return raw_bytes.decode("utf-8"), None
        except UnicodeDecodeError:
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

    system_prompt = load_prompt(PromptId.INGEST_RELEVANCE_V1)

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


from app.progress import update_progress

async def ingest_node(state: PipelineState) -> IngestOutput:
    """Ingest input, extract/clean text, mask PII, and classify relevance for routing."""
    print("--- INGEST NODE ---")
    update_progress(state.get("job_id"), "ingest", 10, "PROCESSING")
    # Trust the file_type determined by detect_file_type

    file_type = str(state.get("file_type", "")).strip().lower()

    raw_inputs = state.get("raw_inputs") or []
    if raw_inputs:
        source_docs_by_id = {
            doc.get("document_id"): dict(doc)
            for doc in state.get("source_documents", [])
            if doc.get("document_id")
        }
        extracted_documents = []
        combined_text_parts = []
        aggregate_stats = {"emails": 0, "phones": 0, "credit_cards": 0, "api_keys": 0}

        try:
            for raw_input in raw_inputs:
                document_id = raw_input.get("document_id")
                filename = raw_input.get("filename") or document_id or "unknown_file"
                docx_paragraphs = None
                if raw_input.get("file_type") == "docx":
                    res = _extract_docx(raw_input.get("raw_bytes") or b"")
                    if len(res) == 3:
                        text, extraction_error, docx_paragraphs = res
                    else:
                        text, extraction_error = res
                else:
                    text, extraction_error = _extract_from_bytes(
                        raw_input.get("raw_bytes") or b"", raw_input.get("file_type", "")
                    )
                if extraction_error:
                    return _build_output(
                        raw_text=None, is_useful=False, relevance_score=0.0, status="rejected",
                        error=f"{extraction_error} for '{filename}'",
                    )
                normalized_text = text.strip()
                if len(normalized_text) < MIN_TEXT_LENGTH:
                    return _build_output(
                        raw_text=None, is_useful=False, relevance_score=0.0, status="rejected",
                        error=f"INGEST_EMPTY: text too short for '{filename}' ({len(normalized_text)} chars)",
                    )
                if settings.ENABLE_PII_MASKING:
                    normalized_text, stats = _mask_pii(normalized_text)
                    for key, value in stats.items():
                        aggregate_stats[key] += value

                source_doc = source_docs_by_id.setdefault(document_id, {})
                source_doc.update({
                    "document_id": document_id,
                    "filename": filename,
                    "file_type": raw_input.get("file_type"),
                    "mime_type": raw_input.get("mime_type"),
                    "sha256_hash": raw_input.get("sha256_hash"),
                    "text": normalized_text,
                    "docx_paragraphs": docx_paragraphs,
                    "language": raw_input.get("language") or state.get("language"),
                })
                extracted_documents.append(source_doc)
                combined_text_parts.append(normalized_text)

            combined_text = "\n\n".join(combined_text_parts)
            relevance = await _run_relevance_check(combined_text[:RELEVANCE_SNIPPET_CHARS])
            pii_stats = {key: value for key, value in aggregate_stats.items() if value > 0} or None
            if not relevance.is_useful:
                return _build_output(
                    raw_text=combined_text, is_useful=False, relevance_score=relevance.relevance_score,
                    status="rejected", error=f"DOCUMENT_REJECTED: {relevance.reason}", pii_stats=pii_stats,
                )
            return {
                "raw_text": combined_text,
                "source_documents": extracted_documents,
                "is_useful": True,
                "relevance_score": relevance.relevance_score,
                "status": "ready_for_chunking",
                "error": None,
                "pii_stats": pii_stats,
            }
        except Exception as exc:
            logger.exception("Unhandled multipart ingest failure")
            return _build_output(
                raw_text=None, is_useful=False, relevance_score=0.0, status="rejected",
                error=f"INGEST_FAILED: {exc}",
            )

    if file_type == "audio":
        return _build_output(
            raw_text=state.get("raw_text"),
            is_useful=True,
            relevance_score=1.0,
            status="to_transcribe",
            error=None,
        )

    try:
        docx_paragraphs = None
        if file_type == "docx":
            res = _extract_docx(state.get("raw_bytes", b""))
            if len(res) == 3:
                extracted_text, extraction_error, docx_paragraphs = res
            else:
                extracted_text, extraction_error = res
        else:
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

        if settings.ENABLE_PII_MASKING:
            masked_text, stats = _mask_pii(normalized_text)
            non_zero_stats = {k: v for k, v in stats.items() if v > 0}
            if non_zero_stats:
                logger.info("PII masking completed: %s", json.dumps(non_zero_stats))
        else:
            masked_text = normalized_text
            stats = None

        if len(masked_text) < MIN_TEXT_LENGTH:
            return _build_output(
                raw_text=masked_text or None,
                is_useful=False,
                relevance_score=0.0,
                status="rejected",
                error=f"INGEST_EMPTY: text too short after masking ({len(masked_text)} chars)",
                pii_stats=stats,
            )

        relevance = await _run_relevance_check(masked_text[:RELEVANCE_SNIPPET_CHARS])
        if not relevance.is_useful:
            return _build_output(
                raw_text=masked_text,
                is_useful=False,
                relevance_score=relevance.relevance_score,
                status="rejected",
                error=f"DOCUMENT_REJECTED: {relevance.reason}",
                pii_stats=stats,
            )

        out = _build_output(
            raw_text=masked_text,
            is_useful=True,
            relevance_score=relevance.relevance_score,
            status="ready_for_chunking",
            error=None,
            pii_stats=stats,
        )
        # Populate source_documents metadata so parse_to_chunks can use it
        doc_source = state.get("metadata", {}) or {}
        out["source_documents"] = [
            {
                "document_id": state.get("job_id") or "default_doc",
                "filename": doc_source.get("filename") or "document.docx",
                "file_type": file_type,
                "text": masked_text,
                "docx_paragraphs": docx_paragraphs,
                "language": state.get("language"),
            }
        ]
        return out
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
