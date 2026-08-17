from app.schemas.pipeline_state import PipelineState
from app.schemas.items import (
    ExtractedRequirement,
    SourceChunk,
    EvidenceSpan,
    FunctionalRequirement,
    RequirementType,
    RequirementDisposition,
    QualityIssue,
    PipelineWarning,
)
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from pydantic import BaseModel, Field
from typing import List, Optional, Any
import inspect
import re
import asyncio
import traceback
import json
import logging

from app.config import settings
from app.services.semantic_quality import infer_requirement_priority
from app.services.audio_semantics import (
    audio_text_requires_review,
    is_audio_chunk,
    normalize_audio_requirement_text,
)
from app.utils.json_parsing import loads_with_llm_repair

logger = logging.getLogger(__name__)


_AUDIO_EXTRACTION_CONTEXT = """
This source is a reconstructed speech-transcript window. Preserve the meaning
of the spoken statement even when ASR punctuation is imperfect. Keep one parent
requirement when it contains a list of mandatory input fields, a purpose clause,
or a measurable test condition. Do not create separate requirements for each
field in a list, a "to facilitate/so that" purpose, or a load condition such as
"up to 500 active sessions". Keep compound domain names together (for example,
QR code, LDAP Active Directory, and TLS 1.3). Normalize the requirement text to
English as usual, but copy the evidence quote exactly from the transcript.

Extract an independently testable approval, permission, or access rule
separately from a general capability when it has its own condition. Conversely,
keep a prohibition and its required replacement action together when the source
connects them (for example, "cannot be permanently deleted; must be
soft-deleted and marked as Retired"). Ignore spoken document section headings,
list numbers, and labels such as "Functional Requirements".
""".strip()


def _raw_io_enabled() -> bool:
    """Whether raw LLM input/output may be logged. Always False in production."""
    return bool(settings.DEBUG_LLM_IO) and settings.ENV != "production"


class ExtractionResponse(BaseModel):
    requirements: List[ExtractedRequirement] = Field(
        description="A list of extracted requirements including functional, non-functional, business rules, constraints, etc."
    )


# Allowed labels exactly as requested
ALLOWED_LABELS = {"FR", "NFR", "BR", "Constraint", "Assumption", "Open Question", "Out-of-Scope"}

# Normalization map for common LLM variants
LABEL_MAP = {
    "Functional Requirement": "FR",
    "Functional": "FR",
    "Non-Functional Requirement": "NFR",
    "Non Functional": "NFR",
    "Non-Functional": "NFR",
    "Business Rule": "BR",
    "business_rule": "BR",
    "OpenQuestion": "Open Question",
    "open_question": "Open Question",
    "Out of Scope": "Out-of-Scope",
    "out_of_scope": "Out-of-Scope",
    "Out-of-scope": "Out-of-Scope",
}

DISPOSITION_MAP = {
    "accepted": "accepted",
    "active": "accepted",
    "approved": "accepted",
    "confirmed": "accepted",
    "rejected": "rejected",
    "dropped": "rejected",
    "cancelled": "rejected",
    "canceled": "rejected",
    "declined": "rejected",
    "deferred": "deferred",
    "postponed": "deferred",
    "delayed": "deferred",
    "future": "deferred",
    "proposed": "proposed",
    "tentative": "proposed",
    "suggestion": "proposed",
    "uncertain": "uncertain",
    "ambiguous": "uncertain",
}

REJECTION_PATTERN = re.compile(
    r"\b(decided not to build|decided against|we decided against|we rejected|agreed not to include|"
    r"won't be supporting|won't be building|ruled out|not building|dropped the idea of|"
    r"decided to drop|decided to skip|we are not doing|not doing this|discarded)\b",
    re.IGNORECASE,
)

DEFERRAL_PATTERN = re.compile(
    r"\b(postponed to phase|deferred to phase|revisit in phase|deferred to v2|postpone until next|"
    r"defer to next year|out of scope for (now|phase 1)|pushed to phase|later phase|future sprint)\b",
    re.IGNORECASE,
)

ACTIVE_PROHIBITION_PATTERN = re.compile(
    r"\b(must not|shall not|cannot be deleted|is prohibited|prevent|never allow|disallow|"
    r"ensure .* does not|remain closed|remains closed|stay closed|stays closed|do not allow|"
    r"restricted from|forbidden)\b",
    re.IGNORECASE,
)

def normalize_label(l: str) -> str:
    if not l:
        return "FR"
    if l in ALLOWED_LABELS:
        return l
    mapped = LABEL_MAP.get(l)
    if mapped:
        return mapped
    # Heuristic fallback
    up = str(l).strip()
    if up.lower().startswith("func"): return "FR"
    if "non" in up.lower() or "nfr" in up.lower(): return "NFR"
    if "business" in up.lower() or up.lower() == "br": return "BR"
    if "out" in up.lower() and "scope" in up.lower(): return "Out-of-Scope"
    if "open" in up.lower() and "question" in up.lower(): return "Open Question"
    if "constraint" in up.lower(): return "Constraint"
    if "assumption" in up.lower(): return "Assumption"
    return "FR"

def normalize_disposition(d: Optional[str]) -> RequirementDisposition:
    if not d:
        return "accepted"
    val = str(d).strip().lower()
    return DISPOSITION_MAP.get(val, "accepted")

def is_candidate_label_key(k: str) -> bool:
    """Check if a string explicitly names a requirement label category."""
    if not k or not isinstance(k, str):
        return False
    if k in ALLOWED_LABELS or k in LABEL_MAP:
        return True
    low = k.strip().lower()
    return (
        low.startswith("func")
        or "non" in low
        or "nfr" in low
        or "business" in low
        or low == "br"
        or ("out" in low and "scope" in low)
        or ("open" in low and "question" in low)
        or "constraint" in low
        or "assumption" in low
    )


def normalize_extraction_payload(parsed: Any, chunk: SourceChunk) -> dict:
    """
    Standardize various LLM output shapes into valid ExtractionResponse dict.
    Supports canonical list, bare list, single requirement dict, alternate items/data wrappers,
    shorthand label-key format, and numeric string key maps.
    """
    items = []
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        # 1. Check for standard container keys
        container_keys = ["requirements", "items", "data", "results", "extracted_requirements", "output", "payload"]
        for k in container_keys:
            if k in parsed and isinstance(parsed[k], (list, dict)):
                if isinstance(parsed[k], list):
                    items = parsed[k]
                    break
                elif isinstance(parsed[k], dict):
                    # Dict of requirement objects e.g. {"1": {...}, "2": {...}}
                    items = list(parsed[k].values())
                    break

        # 2. Check if root dict itself is a single requirement object
        if not items and parsed:
            text_keys = ["text", "requirement", "description", "statement", "summary", "name", "title"]
            has_text = any(tk in parsed and isinstance(parsed[tk], str) and parsed[tk].strip() for tk in text_keys)
            if has_text or ("id" in parsed and ("candidate_labels" in parsed or "disposition" in parsed)):
                items = [parsed]

        # 3. Check if the root dict is keyed by numeric IDs {"1": {...}, "2": {...}}
        if not items and parsed and all(str(k).strip().isdigit() for k in parsed.keys()):
            items = list(parsed.values())

        # 4. Check for shorthand label keys {"FR": "...", "NFR": "..."} or {"FR": ["...", "..."]}
        if not items and parsed:
            shorthand_items = []
            for k, v in parsed.items():
                if is_candidate_label_key(k):
                    norm_k = normalize_label(k)
                    if isinstance(v, list):
                        for sub_val in v:
                            if isinstance(sub_val, str):
                                shorthand_items.append({"label": norm_k, "text": sub_val})
                            elif isinstance(sub_val, dict):
                                shorthand_items.append(dict(sub_val, label=sub_val.get("label") or norm_k))
                    elif isinstance(v, str):
                        shorthand_items.append({"label": norm_k, "text": v})
                    elif isinstance(v, dict):
                        shorthand_items.append(dict(v, label=v.get("label") or norm_k))
            if shorthand_items:
                items = shorthand_items

        # 5. Final fallback if dict has any recognized fields
        if not items and parsed:
            if any(k in ("id", "evidence", "confidence") for k in parsed.keys()):
                items = [parsed]

    normalized_reqs = []
    for i, item in enumerate(items):
        # 1. Handle shorthand { "FR": "text" }
        if isinstance(item, dict) and len(item) == 1:
            key = list(item.keys())[0]
            val = item[key]
            if (key in ALLOWED_LABELS or key in LABEL_MAP or normalize_label(key) in ALLOWED_LABELS) and isinstance(val, str):
                item = {
                    "id": i + 1,
                    "text": val,
                    "candidate_labels": [normalize_label(key)],
                    "disposition": "accepted",
                    "confidence": 0.85,
                    "priority": "Medium",
                    "evidence": []
                }

        if not isinstance(item, dict):
            if isinstance(item, str) and item.strip():
                item = {
                    "id": i + 1,
                    "text": item.strip(),
                    "candidate_labels": ["FR"],
                    "disposition": "accepted",
                    "confidence": 0.85,
                    "priority": "Medium",
                    "evidence": []
                }
            else:
                continue

        # 2. Extract basic fields with fallbacks
        req_id = item.get("id") or (i + 1)
        text = item.get("text") or item.get("requirement") or item.get("description") or item.get("statement") or item.get("summary") or item.get("title") or ""
        if not text.strip():
            continue

        # 3. Normalize labels
        raw_labels = item.get("candidate_labels") or item.get("labels") or []
        if isinstance(raw_labels, str):
            raw_labels = [raw_labels]
        if not raw_labels and "label" in item:
            raw_labels = [item["label"]]

        norm_labels = []
        for rl in raw_labels:
            nl = normalize_label(rl)
            if nl not in norm_labels:
                norm_labels.append(nl)
        if not norm_labels:
            norm_labels = ["FR"]

        # 4. Handle evidence
        evidence = item.get("evidence") or []
        if isinstance(evidence, dict):
            evidence = [evidence]
        if not evidence:
            evidence = [{
                "chunk_id": chunk.chunk_id,
                "quote": text[:500],
                "page_number": chunk.page_number,
                "speaker": chunk.speaker,
                "timestamp": str(chunk.start_time_sec) if chunk.start_time_sec is not None else None,
                "document_id": getattr(chunk, "document_id", None),
            }]
        else:
            for ev in evidence:
                if isinstance(ev, dict):
                    if not ev.get("chunk_id"):
                        ev["chunk_id"] = chunk.chunk_id
                    if ev.get("page_number") is None:
                        ev["page_number"] = chunk.page_number
                    if ev.get("speaker") is None:
                        ev["speaker"] = chunk.speaker
                    if ev.get("timestamp") is None and chunk.start_time_sec is not None:
                        ev["timestamp"] = str(chunk.start_time_sec)
                    if ev.get("document_id") is None:
                        ev["document_id"] = getattr(chunk, "document_id", None)

        # 5. Disposition normalization & heuristic source intent validation
        raw_disp = item.get("disposition")
        norm_disp = normalize_disposition(raw_disp)

        combined_context = (
            text + " " + " ".join(
                ev.get("quote", "") if isinstance(ev, dict) else getattr(ev, "quote", "")
                for ev in evidence
            )
        )

        if ACTIVE_PROHIBITION_PATTERN.search(combined_context) and not REJECTION_PATTERN.search(combined_context):
            norm_disp = "accepted"
        elif REJECTION_PATTERN.search(combined_context):
            norm_disp = "rejected"
            if "Out-of-Scope" not in norm_labels:
                norm_labels.append("Out-of-Scope")
        elif DEFERRAL_PATTERN.search(combined_context):
            norm_disp = "deferred"
            if "Out-of-Scope" not in norm_labels:
                norm_labels.append("Out-of-Scope")

        # 6. Build full object
        extraction_type = item.get("extraction_type")
        if extraction_type not in ("explicit", "implied"):
            extraction_type = None

        audio_requirement = is_audio_chunk(chunk)
        if audio_requirement:
            text = normalize_audio_requirement_text(text)
        priority = infer_requirement_priority(text, item.get("priority"))
        needs_review = bool(item.get("needs_review"))
        review_reason = item.get("review_reason")
        if audio_requirement and audio_text_requires_review(text):
            needs_review = True
            marker = "[ASR_INCOMPLETE_FRAGMENT]"
            review_reason = f"{review_reason or ''} {marker}".strip()

        normalized_reqs.append({
            "id": req_id,
            "text": text,
            "actor": item.get("actor") or item.get("user") or item.get("role"),
            "goal": item.get("goal") or item.get("action") or item.get("objective"),
            "disposition": norm_disp,
            "candidate_labels": norm_labels,
            "confidence": item.get("confidence") or 0.85,
            "evidence": evidence,
            "priority": priority,
            "extraction_type": extraction_type,
            "needs_review": needs_review,
            "review_reason": review_reason,
        })

    return {"requirements": normalized_reqs}


def preprocess_text(text: str) -> str:
    """
    Clean raw text before sending to LLM.
    Preserves uppercase acronyms (e.g., ER, AH).
    """
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    # Remove common speech filler words only if they are lowercase 
    # to avoid damaging technical acronyms like ER diagram or AH header.
    text = re.sub(r"\b(uh|um|er|ah)\b", "", text)
    return text


def align_quote_with_kind(quote: str, original_text: str) -> tuple[str, str]:
    """Align ``quote`` to ``original_text`` and report how it matched."""
    if not quote or not original_text:
        return (original_text[:200] if original_text else "", "fallback")

    # 1. Exact match
    if quote in original_text:
        return (quote, "exact")

    # 2. Normalized match (ignoring case and whitespace).
    words = re.findall(r"\w+", quote.lower())
    if words:
        pattern = r"\s*".join([re.escape(w) for w in words])
        match = re.search(pattern, original_text, re.IGNORECASE)
        if match:
            return (match.group(0), "fuzzy")

    # 3. Fallback: a valid substring from the source (weak evidence).
    return (original_text[:min(200, len(original_text))], "fallback")


def align_quote_to_source(quote: str, original_text: str) -> str:
    """Backward-compatible wrapper returning only the aligned quote string."""
    aligned, _kind = align_quote_with_kind(quote, original_text)
    return aligned


class ChunkExtractionOutcome:
    """Typed outcome of extracting requirements from a single chunk."""

    def __init__(
        self,
        chunk_id: str,
        requirements: List[ExtractedRequirement],
        outcome_type: str,
        error_message: Optional[str] = None,
        error_code: Optional[str] = None,
    ):
        self.chunk_id = chunk_id
        self.requirements = requirements
        self.outcome_type = outcome_type
        self.error_message = error_message
        self.error_code = error_code

    def __iter__(self):
        return iter(self.requirements)

    def __len__(self):
        return len(self.requirements)

    def __bool__(self):
        return bool(self.requirements)

    def __getitem__(self, item):
        return self.requirements[item]


async def process_chunk(llm, chunk: SourceChunk) -> ChunkExtractionOutcome:
    """
    Process one SourceChunk using LLM.
    Returns typed ChunkExtractionOutcome to isolate and classify any failures.
    """
    clean_text = preprocess_text(chunk.text)
    if not clean_text:
        return ChunkExtractionOutcome(
            chunk_id=chunk.chunk_id,
            requirements=[],
            outcome_type="SUCCESS_NO_REQUIREMENTS",
        )

    try:
        system_text = load_prompt(PromptId.EXTRACT_REQUIREMENTS_V2)
        audio_context = f"\n\n{_AUDIO_EXTRACTION_CONTEXT}" if is_audio_chunk(chunk) else ""
        user_text = f"Extract requirements from this text:\n\n{clean_text}{audio_context}"

        try:
            raw = await llm.ainvoke([
                ("system", system_text),
                ("user", user_text)
            ])
            content = getattr(raw, "content", None) or str(raw)

            if _raw_io_enabled():
                logger.debug(
                    "[extract][chunk=%s] raw model output preview: %s",
                    chunk.chunk_id, content[:500],
                )

            # Parse JSON tolerantly
            try:
                parsed = await loads_with_llm_repair(content, llm)
            except Exception as je:
                logger.warning(
                    "[extract][chunk=%s] JSON parse failed after repair: %s",
                    chunk.chunk_id, type(je).__name__,
                )
                if _raw_io_enabled():
                    logger.debug("[extract][chunk=%s] unparseable content: %s", chunk.chunk_id, content)
                return ChunkExtractionOutcome(
                    chunk_id=chunk.chunk_id,
                    requirements=[],
                    outcome_type="PARSE_FAILURE",
                    error_message=f"JSON parse error: {str(je)}",
                    error_code="EXTRACT_PARSE_FAILURE",
                )

            # Normalize shorthand before validation
            normalized = normalize_extraction_payload(parsed, chunk)

            # Validate against schema
            try:
                response = ExtractionResponse.model_validate(normalized)
            except Exception as ve:
                logger.warning(
                    "[extract][chunk=%s] validation error: %s",
                    chunk.chunk_id, type(ve).__name__,
                )
                return ChunkExtractionOutcome(
                    chunk_id=chunk.chunk_id,
                    requirements=[],
                    outcome_type="VALIDATION_FAILURE",
                    error_message=f"Schema validation error: {str(ve)}",
                    error_code="EXTRACT_VALIDATION_FAILURE",
                )

        except asyncio.TimeoutError as te:
            logger.warning("LLM extraction timed out for chunk %s", chunk.chunk_id)
            return ChunkExtractionOutcome(
                chunk_id=chunk.chunk_id,
                requirements=[],
                outcome_type="MODEL_TIMEOUT",
                error_message=f"LLM request timed out: {str(te)}",
                error_code="EXTRACT_MODEL_TIMEOUT",
            )
        except Exception as llm_err:
            logger.warning(
                "LLM extraction failed for chunk %s: %s: %s",
                chunk.chunk_id, type(llm_err).__name__, str(llm_err),
            )
            return ChunkExtractionOutcome(
                chunk_id=chunk.chunk_id,
                requirements=[],
                outcome_type="MODEL_FAILURE",
                error_message=f"LLM provider error ({type(llm_err).__name__}): {str(llm_err)}",
                error_code="EXTRACT_PROVIDER_FAILURE",
            )

        if not response or not getattr(response, "requirements", None):
            return ChunkExtractionOutcome(
                chunk_id=chunk.chunk_id,
                requirements=[],
                outcome_type="SUCCESS_NO_REQUIREMENTS",
            )

        reqs = response.requirements

        for r in reqs:
            penalty = 1.0
            if not r.evidence:
                r.evidence = [EvidenceSpan(
                    chunk_id=chunk.chunk_id,
                    quote=chunk.text[:min(200, len(chunk.text))],
                    page_number=chunk.page_number,
                    speaker=chunk.speaker,
                    timestamp=str(chunk.start_time_sec) if chunk.start_time_sec is not None else None,
                    document_id=getattr(chunk, "document_id", None),
                    origin="fallback",
                )]
                r.needs_review = True
                r.review_reason = (r.review_reason or "") + " [AUTO_FIX: Missing evidence quote fallback to source snippet]"
                penalty = min(penalty, 0.7)
            else:
                for ev in r.evidence:
                    ev.chunk_id = chunk.chunk_id
                    original_quote = ev.quote
                    aligned_quote, kind = align_quote_with_kind(original_quote, chunk.text)

                    if kind == "fuzzy":
                        ev.quote = aligned_quote
                        r.needs_review = True
                        r.review_reason = (r.review_reason or "") + f" [AUTO_FIX: Quote aligned to source. Original: '{original_quote[:50]}...']"
                        penalty = min(penalty, 0.9)
                    elif kind == "fallback":
                        ev.quote = aligned_quote
                        ev.origin = "fallback"
                        r.needs_review = True
                        r.review_reason = (r.review_reason or "") + " [AUTO_FIX: Quote replaced with source snippet (no match found)]"
                        penalty = min(penalty, 0.7)

                    if ev.page_number is None:
                        ev.page_number = chunk.page_number
                    if ev.speaker is None:
                        ev.speaker = chunk.speaker
                    if ev.timestamp is None and chunk.start_time_sec is not None:
                        ev.timestamp = str(chunk.start_time_sec)
                    if ev.document_id is None:
                        ev.document_id = getattr(chunk, "document_id", None)

            if penalty < 1.0:
                try:
                    r.confidence = round(max(0.0, min(1.0, float(r.confidence) * penalty)), 4)
                except (TypeError, ValueError):
                    r.confidence = round(0.5 * penalty, 4)

        return ChunkExtractionOutcome(
            chunk_id=chunk.chunk_id,
            requirements=reqs,
            outcome_type="SUCCESS_WITH_REQUIREMENTS",
        )
    except Exception as e:
        logger.exception("Unexpected error processing chunk %s: %s", chunk.chunk_id, e)
        return ChunkExtractionOutcome(
            chunk_id=chunk.chunk_id,
            requirements=[],
            outcome_type="MODEL_FAILURE",
            error_message=f"Unexpected extraction error: {str(e)}",
            error_code="EXTRACT_UNEXPECTED_FAILURE",
        )


def project_legacy_requirements(reqs: List[ExtractedRequirement]) -> List[FunctionalRequirement]:
    """
    Project ExtractedRequirement list to legacy FunctionalRequirement list for backward compatibility.
    Only includes items that have 'FR' in their candidate_labels.
    """
    legacy_reqs = []
    for r in reqs:
        if "FR" in r.candidate_labels:
            legacy_reqs.append(FunctionalRequirement(
                id=r.id,
                text=r.text,
                actor=r.actor or "System",
                goal=r.goal or "",
                disposition=r.disposition,
                source_hint=r.evidence[0].quote[:100] if r.evidence else ""
            ))
    return legacy_reqs


from app.progress import update_progress


async def extract_node(state: PipelineState) -> dict:
    """
    Extract requirements from chunks (or raw_text fallback) using LLM.
    Supports FR, NFR, BR, Constraint, Assumption, Open Question, and Out-of-Scope.
    """
    logger.info("--- EXTRACT NODE ---")
    job_id = state.get("job_id")
    update_progress(job_id, "extract", 45, "PROCESSING")
    
    # 1. Get input chunks
    chunks = state.get("chunks", [])
    
    # Fallback to raw_text if no chunks present (backward compatibility)
    if not chunks:
        raw_text = state.get("raw_text")
        if raw_text:
            chunks = [SourceChunk(
                chunk_id="raw_fallback",
                text=raw_text,
                start_char=0,
                end_char=len(raw_text)
            )]
    
    if not chunks:
        return {
            "error": "EXTRACT_FAILED: No chunks or raw text provided",
            "status": "error"
        }

    try:
        # 2. Initialize LLM
        llm = get_llm()

        # 3. Bounded concurrency setup
        concurrency = max(1, int(getattr(settings, "EXTRACTION_CONCURRENCY", getattr(settings, "LLM_MAX_CONCURRENCY", 3))))
        semaphore = asyncio.Semaphore(concurrency)

        async def _guarded_process_chunk(c: SourceChunk) -> ChunkExtractionOutcome:
            async with semaphore:
                return await process_chunk(llm, c)

        # 4. Process Chunks in Parallel with bounded concurrency
        tasks = [_guarded_process_chunk(chunk) for chunk in chunks]
        outcomes: List[ChunkExtractionOutcome] = await asyncio.gather(*tasks)

        # 5. Compute telemetry counters
        chunks_total = len(chunks)
        chunks_succeeded = sum(1 for o in outcomes if o.outcome_type in ("SUCCESS_WITH_REQUIREMENTS", "SUCCESS_NO_REQUIREMENTS"))
        chunks_empty_valid = sum(1 for o in outcomes if o.outcome_type == "SUCCESS_NO_REQUIREMENTS")
        chunks_model_failed = sum(1 for o in outcomes if o.outcome_type in ("MODEL_FAILURE", "MODEL_TIMEOUT"))
        chunks_parse_failed = sum(1 for o in outcomes if o.outcome_type == "PARSE_FAILURE")
        chunks_validation_failed = sum(1 for o in outcomes if o.outcome_type == "VALIDATION_FAILURE")

        extracted_reqs = [
            req
            for outcome in outcomes
            for req in outcome.requirements
        ]
        requirements_extracted = len(extracted_reqs)

        telemetry = {
            "chunks_total": chunks_total,
            "chunks_succeeded": chunks_succeeded,
            "chunks_empty_valid": chunks_empty_valid,
            "chunks_model_failed": chunks_model_failed,
            "chunks_parse_failed": chunks_parse_failed,
            "chunks_validation_failed": chunks_validation_failed,
            "requirements_extracted": requirements_extracted,
        }
        logger.info("[extract][job=%s] Extraction telemetry: %s", job_id, telemetry)

        existing_warnings = state.get("warnings", []) or []
        existing_quality_issues = state.get("quality_issues", []) or []

        # 6. Handle failure vs success vs empty states
        if not extracted_reqs:
            # Case A: ALL chunks failed due to technical errors
            if chunks_total > 0 and (chunks_model_failed + chunks_parse_failed + chunks_validation_failed) == chunks_total:
                first_failed = next((o for o in outcomes if o.error_message), None)
                err_code = (first_failed.error_code if first_failed else None) or (
                    "EXTRACT_PROVIDER_FAILURE" if chunks_model_failed > 0
                    else ("EXTRACT_PARSE_FAILURE" if chunks_parse_failed > 0 else "EXTRACT_VALIDATION_FAILURE")
                )
                err_msg = (
                    f"{err_code}: All {chunks_total} extraction chunk(s) failed. "
                    f"Last error: {first_failed.error_message if first_failed else 'unknown technical failure'}."
                )
                logger.error("[extract][job=%s] %s", job_id, err_msg)
                return {
                    "extracted_requirements": [],
                    "functional_requirements": [],
                    "error": err_msg,
                    "error_code": err_code,
                    "status": "error",
                    "extraction_telemetry": telemetry,
                }

            # Case B: All chunks ran cleanly and legitimately found no requirements
            new_warnings = [
                {"node_name": "extract", "code": "EXTRACT_EMPTY", "message": "No requirements found in the provided content."}
            ]

            new_quality_issues = []
            if state.get("is_useful"):
                qi = QualityIssue(
                    item_id=0,
                    item_type="requirement",
                    severity="high",
                    rule_violated="USEFUL_INPUT_WITH_EMPTY_EXTRACTION",
                    details="Document was accepted as useful but no requirements were extracted."
                )
                new_quality_issues.append(qi)

            return {
                "extracted_requirements": [],
                "functional_requirements": [],
                "warnings": existing_warnings + new_warnings,
                "quality_issues": existing_quality_issues + new_quality_issues,
                "status": "partial",
                "extraction_telemetry": telemetry,
            }

        # 7. Requirements extracted!
        # Normalize IDs (1, 2, 3...)
        for i, r in enumerate(extracted_reqs, start=1):
            r.id = i

        # Legacy Projection
        legacy_reqs = project_legacy_requirements(extracted_reqs)

        # Warnings for partial chunk failures or weak evidence
        new_warnings = []
        if (chunks_model_failed + chunks_parse_failed + chunks_validation_failed) > 0:
            failed_count = chunks_model_failed + chunks_parse_failed + chunks_validation_failed
            new_warnings.append({
                "node_name": "extract",
                "code": "EXTRACT_PARTIAL_CHUNK_FAILURE",
                "message": f"Extraction succeeded with {requirements_extracted} requirements, but {failed_count} chunk(s) failed.",
            })

        weak = [
            r for r in extracted_reqs
            if r.review_reason and (
                "fallback to source snippet" in r.review_reason
                or "Quote replaced with source snippet" in r.review_reason
            )
        ]
        if weak:
            new_warnings.append({
                "node_name": "extract",
                "code": "EXTRACT_WEAK_EVIDENCE",
                "message": (
                    f"{len(weak)} requirement(s) fell back to a source snippet for "
                    "evidence and need review."
                ),
            })

        return {
            "extracted_requirements": extracted_reqs,
            "functional_requirements": legacy_reqs,
            "status": "success",
            "warnings": existing_warnings + new_warnings,
            "extraction_telemetry": telemetry,
        }

    except Exception as e:
        logger.exception("Extract node fatal failure: %s: %s", type(e).__name__, e)
        return {
            "extracted_requirements": [],
            "functional_requirements": [],
            "error": f"EXTRACT_LLM_FAILURE: {type(e).__name__}: {repr(e)}",
            "status": "error"
        }
