from app.schemas.pipeline_state import PipelineState
from app.schemas.items import ExtractedRequirement, SourceChunk, EvidenceSpan, FunctionalRequirement, RequirementType
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Optional
import re
import asyncio


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

async def process_chunk(chain, chunk: SourceChunk) -> List[ExtractedRequirement]:
    """
    Process one SourceChunk using LLM.
    """
    # Preprocess the chunk text for the LLM
    clean_text = preprocess_text(chunk.text)
    
    if not clean_text:
        return []

    try:
        response = await chain.ainvoke({"text": clean_text})
        
        if not response or not response.requirements:
            return []

        reqs = response.requirements

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
        print(f"Error processing chunk {chunk.chunk_id}: {e}")
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
        structured_llm = llm.with_structured_output(ExtractionResponse)
        
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
        
        chain = prompt | structured_llm

        # 3. Process Chunks in Parallel
        tasks = [process_chunk(chain, chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks)

        # 4. Merge Results
        extracted_reqs = [
            req
            for sublist in results
            for req in sublist
        ]

        if not extracted_reqs:
            return {
                "extracted_requirements": [],
                "functional_requirements": [],
                "warnings": [
                    {"node_name": "extract", "code": "EXTRACT_EMPTY", "message": "No requirements found in the provided content."}
                ],
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
        print(f"Extract node fatal failure: {e}")
        return {
            "extracted_requirements": [],
            "functional_requirements": [],
            "error": f"EXTRACT_LLM_FAILURE: {str(e)}",
            "status": "error"
        }
