"""
Reusable document extraction, text normalization, PII redaction, and relevance check services.
Decoupled from LangGraph nodes to allow safe reuse across API, worker, and background pipelines.
"""

from __future__ import annotations

import io
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import docx
from pydantic import BaseModel, Field

from app.config import settings
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 50
RELEVANCE_SNIPPET_CHARS = 2000

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
OPENAI_KEY_PATTERN = re.compile(r"\b(?:sk-proj-[a-zA-Z0-9_]{32,}|sk-[a-zA-Z0-9]{32,})\b")
AWS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASCA)[A-Z0-9]{16}\b")
GITHUB_KEY_PATTERN = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b")
HF_KEY_PATTERN = re.compile(r"\bhf_[a-zA-Z0-9]{34}\b")
GOOGLE_KEY_PATTERN = re.compile(r"\bAIzaSy[a-zA-Z0-9_\-]{33}\b")
GENERIC_SECRET_PATTERN = re.compile(
    r"(?i)\b(api_key|secret_key|private_key|access_token|db_password)(\s*=\s*['\"]?)([a-zA-Z0-9_\-]{16,})(['\"]?)\b"
)
CREDIT_CARD_CANDIDATE_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


class RelevanceCheckResult(BaseModel):
    is_useful: bool = Field(
        description="True only if the document contains software requirements, technical specifications, or meeting notes."
    )
    relevance_score: float = Field(description="Confidence score between 0 and 1.")
    reason: str = Field(description="Short reason explaining acceptance or rejection.")


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
    digits = [int(c) for c in number if c.isdigit()]
    if not digits:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


def _mask_pii(text: str) -> tuple[str, dict[str, int]]:
    stats: dict[str, int] = {}

    def _replace_card(match: re.Match) -> str:
        val = match.group(0)
        raw_digits = re.sub(r"\D", "", val)
        if 13 <= len(raw_digits) <= 16 and _is_luhn_valid(raw_digits):
            stats["CREDIT_CARD"] = stats.get("CREDIT_CARD", 0) + 1
            return "[REDACTED_CREDIT_CARD]"
        return val

    masked = CREDIT_CARD_CANDIDATE_PATTERN.sub(_replace_card, text)

    def _replace_secret(match: re.Match) -> str:
        key_name = match.group(1)
        stats["API_SECRET"] = stats.get("API_SECRET", 0) + 1
        return f"{key_name}=[REDACTED_SECRET]"

    masked = GENERIC_SECRET_PATTERN.sub(_replace_secret, masked)

    def _replace_known_token(match: re.Match, token_name: str) -> str:
        stats[token_name] = stats.get(token_name, 0) + 1
        return f"[REDACTED_{token_name}]"

    masked = OPENAI_KEY_PATTERN.sub(lambda m: _replace_known_token(m, "OPENAI_KEY"), masked)
    masked = AWS_KEY_PATTERN.sub(lambda m: _replace_known_token(m, "AWS_KEY"), masked)
    masked = GITHUB_KEY_PATTERN.sub(lambda m: _replace_known_token(m, "GITHUB_TOKEN"), masked)
    masked = HF_KEY_PATTERN.sub(lambda m: _replace_known_token(m, "HF_TOKEN"), masked)
    masked = GOOGLE_KEY_PATTERN.sub(lambda m: _replace_known_token(m, "GOOGLE_API_KEY"), masked)

    email_matches = EMAIL_PATTERN.findall(masked)
    if email_matches:
        stats["EMAIL"] = len(email_matches)
        masked = EMAIL_PATTERN.sub("[EMAIL]", masked)

    phone_matches = PHONE_PATTERN.findall(masked)
    if phone_matches:
        stats["PHONE"] = len(phone_matches)
        masked = PHONE_PATTERN.sub("[PHONE]", masked)

    return masked, stats


def _extract_pdf(raw_bytes: bytes) -> tuple[str, Optional[str]]:
    if not raw_bytes:
        return "", "INGEST_FAILED: missing PDF bytes"
    try:
        pages: list[str] = []
        with fitz.open(stream=raw_bytes, filetype="pdf") as document:
            for page in document:
                pages.append(page.get_text())
        return "\f".join(pages), None
    except Exception as e:
        return "", f"INGEST_FAILED: PDF extraction error ({e})"


def _extract_docx(raw_bytes: bytes) -> tuple[str, Optional[str], Optional[list[dict]]]:
    if not raw_bytes:
        return "", "INGEST_FAILED: missing DOCX bytes", None
    try:
        doc = docx.Document(io.BytesIO(raw_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    paragraphs.append(row_text)
        text = "\n\n".join(paragraphs)
        paragraphs_data = [{"text": p, "paragraph_index": i} for i, p in enumerate(paragraphs)]
        return _normalize_text(text), None, paragraphs_data
    except Exception as e:
        return "", f"INGEST_FAILED: DOCX extraction error ({e})", None


def _heuristic_relevance(snippet: str) -> RelevanceCheckResult:
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
    return RelevanceCheckResult(
        is_useful=is_useful,
        relevance_score=score,
        reason=reason,
    )


async def _run_relevance_check(text: str) -> RelevanceCheckResult:
    clean_snippet = text.replace("\f", " ")[:RELEVANCE_SNIPPET_CHARS]
    llm = get_llm()
    if llm is None:
        return _heuristic_relevance(clean_snippet)

    try:
        system_prompt = load_prompt(PromptId.INGEST_RELEVANCE_V1)
        user_prompt = f"Classify this snippet and return structured output.\nSnippet:\n{clean_snippet}"
        raw = await llm.ainvoke([
            ("system", system_prompt),
            ("user", user_prompt),
        ])
        content = getattr(raw, "content", None) or str(raw)
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        parsed = json.loads(content)
        return RelevanceCheckResult(
            is_useful=bool(parsed.get("is_useful")),
            relevance_score=max(0.0, min(1.0, float(parsed.get("relevance_score", 0.0)))),
            reason=str(parsed.get("reason", "No reason provided.")).strip(),
        )
    except Exception as e:
        logger.warning("LLM relevance check failed, falling back to heuristic: %s", e)
        return _heuristic_relevance(clean_snippet)
