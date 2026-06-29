from app.schemas.pipeline_state import PipelineState
from app.schemas.items import (
    ExtractedRequirement,
    SourceChunk,
    EvidenceSpan,
    FunctionalRequirement,
    RequirementType,
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
from app.utils.json_parsing import loads_with_llm_repair

logger = logging.getLogger(__name__)


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

def normalize_extraction_payload(parsed: Any, chunk: SourceChunk) -> dict:
    """
    Standardize various LLM output shapes into valid ExtractionResponse dict.
    Supports shorthand label-key format and missing evidence.
    """
    items = []
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = parsed.get("requirements") or parsed.get("items") or []
        if not items and not any(k in ALLOWED_LABELS for k in parsed.keys()):
            # Check if dict itself is a single requirement or shorthand
            pass

    normalized_reqs = []
    for i, item in enumerate(items):
        # 1. Handle shorthand { "FR": "text" }
        if isinstance(item, dict) and len(item) == 1:
            key = list(item.keys())[0]
            val = item[key]
            if key in ALLOWED_LABELS or key in LABEL_MAP or normalize_label(key) in ALLOWED_LABELS:
                item = {
                    "id": i + 1,
                    "text": val,
                    "candidate_labels": [normalize_label(key)],
                    "confidence": 0.85,
                    "priority": "Medium",
                    "evidence": []
                }

        if not isinstance(item, dict):
            continue

        # 2. Extract basic fields with fallbacks
        req_id = item.get("id") or (i + 1)
        text = item.get("text") or ""
        
        # 3. Normalize labels
        raw_labels = item.get("candidate_labels") or []
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
        if not evidence:
            evidence = [{
                "chunk_id": chunk.chunk_id,
                "quote": text[:500], # use text as quote if missing
                "page_number": chunk.page_number,
                "speaker": chunk.speaker,
                "timestamp": str(chunk.start_time_sec) if chunk.start_time_sec is not None else None
            }]
        else:
            # Ensure every evidence has chunk_id
            for ev in evidence:
                if not ev.get("chunk_id"):
                    ev["chunk_id"] = chunk.chunk_id

        # 5. Build full object
        extraction_type = item.get("extraction_type")
        if extraction_type not in ("explicit", "implied"):
            extraction_type = None
        normalized_reqs.append({
            "id": req_id,
            "text": text,
            "actor": item.get("actor"),
            "goal": item.get("goal"),
            "candidate_labels": norm_labels,
            "confidence": item.get("confidence") or 0.85,
            "evidence": evidence,
            "priority": item.get("priority") or "Medium",
            "extraction_type": extraction_type,
            "needs_review": item.get("needs_review") or False,
            "review_reason": item.get("review_reason")
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
    """Align ``quote`` to ``original_text`` and report how it matched.

    Returns ``(aligned_quote, kind)`` where ``kind`` is one of:
      * ``"exact"``    — the quote already appears verbatim in the source.
      * ``"fuzzy"``    — a case/whitespace-insensitive match was found.
      * ``"fallback"`` — no real match; a source snippet is substituted.

    The ``kind`` lets callers grade evidence quality (e.g. lower confidence and
    flag review for ``fallback``) — important because a fallback snippet is, by
    construction, still a substring of the chunk and would otherwise look like a
    successful alignment.
    """
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

async def process_chunk(llm, chunk: SourceChunk) -> List[ExtractedRequirement]:
    """
    Process one SourceChunk using LLM.
    """
    # Preprocess the chunk text for the LLM
    clean_text = preprocess_text(chunk.text)
    
    if not clean_text:
        return []

    try:
        # Load the strict extraction prompt from centralized registry (v2:
        # verbatim-quote grounding + explicit/implied marker).
        system_text = load_prompt(PromptId.EXTRACT_REQUIREMENTS_V2)

        user_text = f"Extract requirements from this text:\n\n{clean_text}"
        
        # Call LLM with strict instructions
        try:
            # We use ainvoke directly for better control over raw output
            raw = await llm.ainvoke([
                ("system", system_text),
                ("user", user_text)
            ])
            content = getattr(raw, "content", None) or str(raw)

            # Raw model output is only ever logged at DEBUG, and never in
            # production (guarded) — it can contain document text.
            if _raw_io_enabled():
                logger.debug(
                    "[extract][chunk=%s] raw model output preview: %s",
                    chunk.chunk_id, content[:500],
                )

            # Parse JSON tolerantly, with ONE LLM repair round on malformed
            # output, so a single bad chunk never crashes the whole pass.
            try:
                parsed = await loads_with_llm_repair(content, llm)
            except Exception as je:
                logger.warning(
                    "[extract][chunk=%s] JSON parse failed after repair: %s",
                    chunk.chunk_id, type(je).__name__,
                )
                if _raw_io_enabled():
                    logger.debug("[extract][chunk=%s] unparseable content: %s", chunk.chunk_id, content)
                return []

            # NORMALIZE shorthand before validation
            normalized = normalize_extraction_payload(parsed, chunk)

            try:
                response = ExtractionResponse.model_validate(normalized)
            except Exception as ve:
                # Do not log the normalized payload — it embeds source text.
                logger.warning(
                    "[extract][chunk=%s] validation error: %s",
                    chunk.chunk_id, type(ve).__name__,
                )
                return []
        except Exception as llm_err:
            logger.warning(
                "LLM extraction failed for chunk %s: %s",
                chunk.chunk_id, type(llm_err).__name__,
            )
            return []

        if not response or not getattr(response, "requirements", None):
            return []

        reqs = response.requirements

        # Enrich with chunk metadata and enforce evidence. Confidence is reduced
        # in proportion to how much we had to repair the evidence:
        #   * exact quote already present in chunk  -> no penalty
        #   * quote aligned to a near-match in chunk -> slight penalty (x0.9)
        #   * quote replaced by a source snippet     -> stronger penalty (x0.7)
        #   * no evidence at all (snippet fallback)   -> stronger penalty (x0.7)
        # We never silently drop a requirement for weak evidence — we keep it and
        # flag needs_review so downstream grounding/quality can act on it.
        for r in reqs:
            penalty = 1.0
            if not r.evidence:
                # Fallback: link a source snippet so the requirement stays grounded.
                r.evidence = [EvidenceSpan(
                    chunk_id=chunk.chunk_id,
                    quote=chunk.text[:min(200, len(chunk.text))],  # Snippet from ORIGINAL text
                    page_number=chunk.page_number,
                    speaker=chunk.speaker,
                    timestamp=str(chunk.start_time_sec) if chunk.start_time_sec is not None else None
                )]
                r.needs_review = True
                r.review_reason = (r.review_reason or "") + " [AUTO_FIX: Missing evidence quote fallback to source snippet]"
                penalty = min(penalty, 0.7)
            else:
                # Update evidence with chunk metadata and ALIGN quotes to source.
                for ev in r.evidence:
                    ev.chunk_id = chunk.chunk_id

                    # ALIGNMENT CHECK — grade by match kind.
                    original_quote = ev.quote
                    aligned_quote, kind = align_quote_with_kind(original_quote, chunk.text)

                    if kind == "fuzzy":
                        ev.quote = aligned_quote
                        r.needs_review = True
                        r.review_reason = (r.review_reason or "") + f" [AUTO_FIX: Quote aligned to source. Original: '{original_quote[:50]}...']"
                        penalty = min(penalty, 0.9)
                    elif kind == "fallback":
                        ev.quote = aligned_quote
                        r.needs_review = True
                        r.review_reason = (r.review_reason or "") + " [AUTO_FIX: Quote replaced with source snippet (no match found)]"
                        penalty = min(penalty, 0.7)

                    if ev.page_number is None:
                        ev.page_number = chunk.page_number
                    if ev.speaker is None:
                        ev.speaker = chunk.speaker
                    if ev.timestamp is None and chunk.start_time_sec is not None:
                        ev.timestamp = str(chunk.start_time_sec)

            if penalty < 1.0:
                try:
                    r.confidence = round(max(0.0, min(1.0, float(r.confidence) * penalty)), 4)
                except (TypeError, ValueError):
                    r.confidence = round(0.5 * penalty, 4)

        return reqs
    except Exception as e:
        print(f"Error processing chunk {chunk.chunk_id}: {type(e).__name__}: {repr(e)}")
        traceback.print_exc()
        return []


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
                source_hint=r.evidence[0].quote[:100] if r.evidence else ""
            ))
    return legacy_reqs

from app.progress import update_progress

async def extract_node(state: PipelineState) -> dict:
    """
    Extract requirements from chunks (or raw_text fallback) using LLM.
    Supports FR, NFR, BR, Constraint, Assumption, Open Question, and Out-of-Scope.
    """
    print("--- EXTRACT NODE ---")
    update_progress(state.get("job_id"), "extract", 45, "PROCESSING")
    
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

        # 3. Process Chunks in Parallel using robust per-chunk invocation
        tasks = [process_chunk(llm, chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks)

        # 4. Merge Results
        extracted_reqs = [
            req
            for sublist in results
            for req in sublist
        ]

        if not extracted_reqs:
            new_warnings = [
                {"node_name": "extract", "code": "EXTRACT_EMPTY", "message": "No requirements found in the provided content."}
            ]
            existing_warnings = state.get("warnings", []) or []

            new_quality_issues = []
            # If the document was accepted as useful, add a high-severity quality issue
            if state.get("is_useful"):
                qi = QualityIssue(
                    item_id=0,
                    item_type="requirement",
                    severity="high",
                    rule_violated="USEFUL_INPUT_WITH_EMPTY_EXTRACTION",
                    details="Document was accepted as useful but no requirements were extracted."
                )
                new_quality_issues.append(qi)
            
            existing_quality_issues = state.get("quality_issues", []) or []

            return {
                "extracted_requirements": [],
                "functional_requirements": [],
                "warnings": existing_warnings + new_warnings,
                "quality_issues": existing_quality_issues + new_quality_issues,
                "status": "partial"
            }

        # 5. Normalize IDs (1, 2, 3...)

        for i, r in enumerate(extracted_reqs, start=1):
            r.id = i

        # 6. Legacy Projection
        legacy_reqs = project_legacy_requirements(extracted_reqs)

        # 7. Surface an aggregate warning when evidence had to fall back to a
        #    source snippet (weak grounding the reviewer should look at).
        weak = [
            r for r in extracted_reqs
            if r.review_reason and (
                "fallback to source snippet" in r.review_reason
                or "Quote replaced with source snippet" in r.review_reason
            )
        ]
        result: dict = {
            "extracted_requirements": extracted_reqs,
            "functional_requirements": legacy_reqs,
            "status": "success",
        }
        if weak:
            existing_warnings = state.get("warnings", []) or []
            result["warnings"] = existing_warnings + [{
                "node_name": "extract",
                "code": "EXTRACT_WEAK_EVIDENCE",
                "message": (
                    f"{len(weak)} requirement(s) fell back to a source snippet for "
                    "evidence and need review."
                ),
            }]
        return result

    except Exception as e:
        print(f"Extract node fatal failure: {type(e).__name__}: {repr(e)}")
        traceback.print_exc()
        return {
            "extracted_requirements": [],
            "functional_requirements": [],
            "error": f"EXTRACT_LLM_FAILURE: {type(e).__name__}: {repr(e)}",
            "status": "error"
        }
