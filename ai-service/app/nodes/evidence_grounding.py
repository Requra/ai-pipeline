from typing import List
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import ClassifiedRequirement, SourceChunk, QualityIssue


async def evidence_grounding_node(state: PipelineState) -> dict:
    """Validate that each ClassifiedRequirement has grounded evidence.

    Rules:
    - Each requirement must have at least one evidence item.
    - Each evidence item must have a non-empty quote.
    - If chunks are available, each quote should appear in at least one chunk text.
    - On failures, mark needs_review and add QualityIssue entries.
    """
    print("--- EVIDENCE GROUNDING NODE ---")

    classified: List[ClassifiedRequirement] = state.get("classified_requirements", [])
    chunks: List[SourceChunk] = state.get("chunks", [])

    existing_q = state.get("quality_issues", []) or []

    chunk_texts = [c.text for c in chunks] if chunks else []

    new_issues: List[QualityIssue] = []

    for req in classified:
        evid = getattr(req, "evidence", []) or []
        if not evid:
            req.needs_review = True
            req.review_reason = (req.review_reason or "") + " [EVIDENCE_MISSING: No evidence provided]"
            new_issues.append(QualityIssue(
                item_id=req.id,
                item_type="requirement",
                severity="high",
                rule_violated="missing_evidence",
                details="Requirement has no evidence quotes backing it."
            ))
            continue

        # Validate each evidence quote non-empty and present in at least one chunk
        for ev in evid:
            quote = getattr(ev, "quote", "") or ""
            if not quote.strip():
                req.needs_review = True
                req.review_reason = (req.review_reason or "") + " [EVIDENCE_EMPTY_QUOTE]"
                new_issues.append(QualityIssue(
                    item_id=req.id,
                    item_type="requirement",
                    severity="medium",
                    rule_violated="evidence_not_grounded",
                    details="Evidence quote is empty and cannot be grounded to source chunks."
                ))
                continue

            # If chunks available, ensure quote is present in at least one chunk
            if chunk_texts:
                found = any(quote in txt for txt in chunk_texts)
                if not found:
                    req.needs_review = True
                    req.review_reason = (req.review_reason or "") + " [EVIDENCE_NOT_FOUND_IN_CHUNKS]"
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_not_grounded",
                        details=f"Evidence quote not found in any chunk: '{quote[:100]}'"
                    ))

    # Return only the newly created quality issues to avoid reducer duplication
    return {"quality_issues": new_issues}
