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
from pydantic import BaseModel, Field
from typing import List, Optional
import inspect
import re
import asyncio
import traceback
import json


class ExtractionResponse(BaseModel):
    requirements: List[ExtractedRequirement] = Field(
        description="A list of extracted requirements including functional, non-functional, business rules, constraints, etc."
    )


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
def align_quote_to_source(quote: str, original_text: str) -> str:
    """
    Ensure the quote exists in the original source text.
    Try exact match, then normalized match. 
    Returns the best available substring from original_text.
    """
    if not quote or not original_text:
        return original_text[:200] if original_text else ""
    
    # 1. Exact match
    if quote in original_text:
        return quote
    
    # 2. Normalized match (ignoring case and whitespace)
    # Create a mapping of normalized to original
    # For simplicity, we search for the sequence of words
    words = re.findall(r"\w+", quote.lower())
    if words:
        pattern = r"\s*".join([re.escape(w) for w in words])
        match = re.search(pattern, original_text, re.IGNORECASE)
        if match:
            return match.group(0)
            
    # 3. Fallback: Return a valid substring from original text
    return original_text[:min(200, len(original_text))]

async def process_chunk(llm, prompt, chunk: SourceChunk) -> List[ExtractedRequirement]:
    """
    Process one SourceChunk using LLM.
    """
    # Preprocess the chunk text for the LLM
    clean_text = preprocess_text(chunk.text)
    
    if not clean_text:
        return []

    try:
        # Build a strict extraction prompt string (system + user)
        system_text = (
            "You are a senior software requirements analyst.\n"
            "Extract atomic software requirements from the source text.\n"
            "Return valid JSON only. No markdown. No explanation.\n\n"
            "Rules:\n"
            "- Extract functional requirements, non-functional requirements, business rules, constraints, assumptions, open questions, and out-of-scope items.\n"
            "- Every item must include a direct quote copied from the source text.\n"
            "- Every quote must exist exactly or nearly exactly in the source text.\n"
            "- Use ONLY these labels exactly: FR, NFR, BR, Constraint, Assumption, Open Question, Out-of-Scope.\n"
            "- If unsure, set needs_review=true.\n"
            "- Do not invent requirements.\n"
            "- Do not return empty requirements when the text clearly contains software requirements.\n\n"
            "Return JSON exactly in this shape: {\"requirements\": [...]}"
        )

        user_text = f"Extract requirements from this text:\n\n{clean_text}"
        
        # Call LLM with strict instructions
        try:
            # We use ainvoke directly for better control over raw output
            raw = await llm.ainvoke([
                ("system", system_text),
                ("user", user_text)
            ])
            content = getattr(raw, "content", None) or str(raw)
            
            # Debug preview: log first 500 chars
            try:
                preview = content[:500]
                print(f"[extract][chunk={chunk.chunk_id}] raw model output preview: {preview}")
            except Exception:
                pass

            # Strip common code fences if LLM ignored instructions
            content = content.strip()
            if content.startswith("```"):
                # Handle cases like ```json or just ```
                lines = content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines).strip()

            try:
                parsed = json.loads(content)
            except Exception as je:
                print(f"[extract][chunk={chunk.chunk_id}] JSON parse error: {type(je).__name__}: {repr(je)}")
                print(f"[extract][chunk={chunk.chunk_id}] raw content: {content}")
                return []

            try:
                response = ExtractionResponse.model_validate(parsed)
            except Exception as ve:
                print(f"[extract][chunk={chunk.chunk_id}] validation error: {type(ve).__name__}: {repr(ve)}")
                return []
        except Exception as llm_err:
            print(f"LLM extraction failed for chunk {chunk.chunk_id}: {type(llm_err).__name__}: {repr(llm_err)}")
            return []

        if not response or not getattr(response, "requirements", None):
            return []

        reqs = response.requirements

        # Normalize labels to allowed set exactly as requested
        LABEL_MAP = {
            "Functional Requirement": "FR",
            "Functional": "FR",
            "Non-Functional Requirement": "NFR",
            "Non Functional": "NFR",
            "Non-Functional": "NFR",
            "Business Rule": "BR",
            "business_rule": "BR",
            "Out of Scope": "Out-of-Scope",
            "Out of scope": "Out-of-Scope",
            "open_question": "Open Question",
        }

        def _normalize_label(l):
            if not l:
                return "FR"
            allowed = {"FR", "NFR", "BR", "Constraint", "Assumption", "Open Question", "Out-of-Scope"}
            if l in allowed:
                return l
            mapped = LABEL_MAP.get(l)
            if mapped:
                return mapped
            # Heuristic fallback if not in map
            up = str(l).strip()
            if up.lower().startswith("func"):
                return "FR"
            if "non" in up.lower() or "nfr" in up.lower():
                return "NFR"
            if "business" in up.lower() or up.lower() == "br":
                return "BR"
            if "out" in up.lower() and "scope" in up.lower():
                return "Out-of-Scope"
            if "open" in up.lower() and "question" in up.lower():
                return "Open Question"
            if "constraint" in up.lower():
                return "Constraint"
            if "assumption" in up.lower():
                return "Assumption"
            return "FR"

        for r in reqs:
            try:
                cl = getattr(r, "candidate_labels", []) or []
                norm = []
                for lab in cl:
                    nl = _normalize_label(lab)
                    if nl not in norm:
                        norm.append(nl)
                r.candidate_labels = norm
            except Exception:
                r.candidate_labels = ["FR"]


        # Enrich with chunk metadata and enforce evidence
        for r in reqs:
            # We ensure each requirement has at least one EvidenceSpan linked to this chunk.
            if not r.evidence:
                # Fallback: link the whole chunk as evidence if LLM failed to be specific
                r.evidence = [EvidenceSpan(
                    chunk_id=chunk.chunk_id,
                    quote=chunk.text[:min(200, len(chunk.text))], # Snippet from ORIGINAL text
                    page_number=chunk.page_number,
                    speaker=chunk.speaker,
                    timestamp=str(chunk.start_time_sec) if chunk.start_time_sec is not None else None
                )]
                r.needs_review = True
                r.review_reason = (r.review_reason or "") + " [AUTO_FIX: Missing evidence quote fallback to source snippet]"
            else:
                # Update evidence with chunk metadata and ALIGN quotes to source
                for ev in r.evidence:
                    ev.chunk_id = chunk.chunk_id
                    
                    # ALIGNMENT CHECK
                    original_quote = ev.quote
                    aligned_quote = align_quote_to_source(original_quote, chunk.text)
                    
                    if aligned_quote != original_quote:
                        ev.quote = aligned_quote
                        r.needs_review = True
                        if aligned_quote in chunk.text:
                            r.review_reason = (r.review_reason or "") + f" [AUTO_FIX: Quote aligned to source. Original: '{original_quote[:50]}...']"
                        else:
                            r.review_reason = (r.review_reason or "") + " [AUTO_FIX: Quote replaced with source snippet (no match found)]"

                    if ev.page_number is None:
                        ev.page_number = chunk.page_number
                    if ev.speaker is None:
                        ev.speaker = chunk.speaker
                    if ev.timestamp is None and chunk.start_time_sec is not None:
                        ev.timestamp = str(chunk.start_time_sec)

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

async def extract_node(state: PipelineState) -> dict:
    """
    Extract requirements from chunks (or raw_text fallback) using LLM.
    Supports FR, NFR, BR, Constraint, Assumption, Open Question, and Out-of-Scope.
    """
    print("--- EXTRACT NODE ---")
    
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

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """
                You are a senior requirements engineer. Extract requirements from the provided text.
                
                Identify and categorize the following:
                - FR (Functional Requirement): What the system must do.
                - NFR (Non-Functional Requirement): Performance, security, usability, etc.
                - BR (Business Rule): Policy or logic that governs the business process.
                - Constraint: Limitations (e.g., specific technology, deadline).
                - Assumption: Things believed to be true but not confirmed.
                - Open Question: Ambiguities needing clarification.
                - Out-of-Scope: Explicitly mentioned items that are NOT being implemented.

                Rules:
                1. Every requirement must be grounded in the text.
                2. Provide at least one direct quote as 'evidence'.
                3. Set 'confidence' (0.0 to 1.0).
                4. If an item is vague, set 'needs_review' to true and provide a 'review_reason'.
                5. Do NOT invent or hallucinate requirements.
                6. Use atomic requirements (one action/fact per item).

                Return structured output only.
                """
            ),
            ("user", "Extract requirements from this text: {text}")
        ])
        
        # 3. Process Chunks in Parallel using robust per-chunk invocation
        tasks = [process_chunk(llm, prompt, chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks)

        # 4. Merge Results
        extracted_reqs = [
            req
            for sublist in results
            for req in sublist
        ]

        if not extracted_reqs:
            warnings = [
                {"node_name": "extract", "code": "EXTRACT_EMPTY", "message": "No requirements found in the provided content."}
            ]

            quality_issues = []
            # If the document was accepted as useful, add a high-severity quality issue
            if state.get("is_useful"):
                qi = QualityIssue(
                    item_id=0,
                    item_type="requirement",
                    severity="high",
                    rule_violated="USEFUL_INPUT_WITH_EMPTY_EXTRACTION",
                    details="Document was accepted as useful but no requirements were extracted."
                )
                quality_issues.append(qi)

            return {
                "extracted_requirements": [],
                "functional_requirements": [],
                "warnings": warnings,
                "quality_issues": quality_issues,
                "status": "partial"
            }

        # 5. Normalize IDs (1, 2, 3...)
        for i, r in enumerate(extracted_reqs, start=1):
            r.id = i

        # 6. Legacy Projection
        legacy_reqs = project_legacy_requirements(extracted_reqs)

        return {
            "extracted_requirements": extracted_reqs,
            "functional_requirements": legacy_reqs,
            "status": "success"
        }

    except Exception as e:
        print(f"Extract node fatal failure: {type(e).__name__}: {repr(e)}")
        traceback.print_exc()
        return {
            "extracted_requirements": [],
            "functional_requirements": [],
            "error": f"EXTRACT_LLM_FAILURE: {type(e).__name__}: {repr(e)}",
            "status": "error"
        }
