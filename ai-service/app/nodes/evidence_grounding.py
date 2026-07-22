from typing import List

from app.progress import update_progress
from app.schemas.items import ClassifiedRequirement, QualityIssue, SourceChunk
from app.schemas.pipeline_state import PipelineState
from app.services.semantic_quality import lexical_support


MIN_GROUNDED_SUPPORT = 0.35


def _review(req: ClassifiedRequirement, marker: str) -> None:
    req.needs_review = True
    req.review_reason = ((req.review_reason or "") + f" [{marker}]").strip()


async def evidence_grounding_node(state: PipelineState) -> dict:
    """Keep only evidence grounded to its declared chunk, document, and claim.

    Retrieval is deliberately treated as candidate generation.  This node is
    the authority that decides which candidates may reach public source_refs.
    """
    print("--- EVIDENCE GROUNDING NODE ---")
    update_progress(state.get("job_id"), "evidence_grounding", 75, "PROCESSING")

    classified: List[ClassifiedRequirement] = state.get("classified_requirements", [])
    chunks: List[SourceChunk] = state.get("chunks", [])
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    existing_q = state.get("quality_issues", []) or []
    new_issues: List[QualityIssue] = []

    for req in classified:
        evidence = list(getattr(req, "evidence", []) or [])
        if not evidence:
            _review(req, "EVIDENCE_MISSING: No evidence provided")
            new_issues.append(QualityIssue(
                item_id=req.id,
                item_type="requirement",
                severity="high",
                rule_violated="missing_evidence",
                details="Requirement has no evidence quotes backing it.",
            ))
            req.quote_support_score = 0.0
            continue

        # When chunks are present, invalid evidence is removed so format_node
        # cannot publish a structurally valid but incorrect source reference.
        verified = []
        for ev in evidence:
            quote = (getattr(ev, "quote", "") or "").strip()
            if not quote:
                _review(req, "EVIDENCE_EMPTY_QUOTE")
                new_issues.append(QualityIssue(
                    item_id=req.id,
                    item_type="requirement",
                    severity="medium",
                    rule_violated="evidence_not_grounded",
                    details="Evidence quote is empty and cannot be grounded.",
                ))
                continue

            if chunks_by_id:
                chunk = chunks_by_id.get(ev.chunk_id)
                if chunk is None:
                    _review(req, "EVIDENCE_CHUNK_NOT_FOUND")
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_chunk_mismatch",
                        details=f"Evidence references missing chunk '{ev.chunk_id}'.",
                    ))
                    continue
                if quote not in chunk.text:
                    _review(req, "EVIDENCE_NOT_FOUND_IN_REFERENCED_CHUNK")
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_not_grounded",
                        details=f"Evidence quote is not present in referenced chunk '{ev.chunk_id}': '{quote[:100]}'",
                    ))
                    continue
                if ev.document_id and ev.document_id != chunk.document_id:
                    _review(req, "EVIDENCE_DOCUMENT_MISMATCH")
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_document_mismatch",
                        details=f"Evidence document '{ev.document_id}' does not match chunk document '{chunk.document_id}'.",
                    ))
                    continue

            support = lexical_support(req.text, quote)
            if ev.origin == "fallback":
                support = min(support, 0.70)
            ev.lexical_score = support
            ev.entailment_score = support
            ev.support_score = support

            if support < MIN_GROUNDED_SUPPORT:
                _review(req, "EVIDENCE_DOES_NOT_SUPPORT_REQUIREMENT")
                new_issues.append(QualityIssue(
                    item_id=req.id,
                    item_type="requirement",
                    severity="medium",
                    rule_violated="evidence_semantic_mismatch",
                    details=f"Evidence in chunk '{ev.chunk_id}' does not support requirement {req.id}.",
                ))
                continue
            verified.append(ev)

        if chunks_by_id:
            req.evidence = verified
        req.quote_support_score = round(
            max((ev.support_score for ev in req.evidence), default=0.0), 4
        )
        if not req.evidence:
            _review(req, "EVIDENCE_MISSING_AFTER_VALIDATION")
            new_issues.append(QualityIssue(
                item_id=req.id,
                item_type="requirement",
                severity="high",
                rule_violated="missing_verified_evidence",
                details="All candidate evidence was rejected during grounding.",
            ))

    return {
        "classified_requirements": classified,
        "quality_issues": existing_q + new_issues,
    }
