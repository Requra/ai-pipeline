from typing import List

from app.progress import update_progress
from app.schemas.items import ClassifiedRequirement, QualityIssue, SourceChunk
from app.schemas.pipeline_state import PipelineState
from app.services.semantic_quality import (
    lexical_support,
    unsupported_numeric_claims,
    unsupported_fact_terms,
    has_polarity_conflict,
    check_different_languages,
)


MIN_GROUNDED_SUPPORT = 0.35
MIN_ASR_CONFIDENCE = 0.65


def _review(req: ClassifiedRequirement, marker: str) -> None:
    req.needs_review = True
    req.review_reason = ((req.review_reason or "") + f" [{marker}]").strip()


def _document_language(source_docs: list[dict], document_id: str | None) -> str | None:
    if not document_id:
        return None
    for doc in source_docs:
        if (doc.get("document_id") or doc.get("source_id")) == document_id:
            return doc.get("language")
    return None


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
    source_docs = state.get("source_documents", []) or []
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
            else:
                chunk = None

            # Get document language metadata
            doc_id = ev.document_id or (chunk.document_id if chunk else None)
            evidence_lang = (
                getattr(chunk, "language", None)
                if chunk is not None
                else None
            ) or _document_language(source_docs, doc_id)
            diff_lang = check_different_languages(
                req.text,
                quote,
                None,
                evidence_lang,
            )
            asr_confidence = (
                getattr(chunk, "asr_confidence", None)
                if chunk is not None
                else None
            )
            low_asr_confidence = (
                asr_confidence is not None
                and float(asr_confidence) < MIN_ASR_CONFIDENCE
            )

            support = lexical_support(req.text, quote)
            if ev.origin == "fallback":
                support = min(support, 0.70)
            ev.lexical_score = support
            ev.entailment_score = support
            ev.support_score = support

            is_accepted = False
            is_partial = False

            if diff_lang:
                is_partial = True
            else:
                if support >= 0.60:
                    numeric_mismatch = bool(unsupported_numeric_claims(req.text, [quote]))
                    unsupported_behavior = bool(unsupported_fact_terms(req.text, [quote]))
                    polarity_conflict = has_polarity_conflict(req.text, [quote])
                    if not (numeric_mismatch or unsupported_behavior or polarity_conflict):
                        is_partial = low_asr_confidence
                        is_accepted = not low_asr_confidence
                    else:
                        _review(req, "EVIDENCE_REJECTED_VERIFICATION_FAIL")
                        new_issues.append(QualityIssue(
                            item_id=req.id,
                            item_type="requirement",
                            severity="medium",
                            rule_violated="evidence_semantic_mismatch",
                            details=f"Evidence in chunk '{ev.chunk_id}' has mismatching numeric, behavior, or polarity.",
                        ))
                        continue
                elif support < 0.25:
                    _review(req, "EVIDENCE_DOES_NOT_SUPPORT_REQUIREMENT")
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_semantic_mismatch",
                        details=f"Evidence in chunk '{ev.chunk_id}' does not support requirement {req.id}.",
                    ))
                    continue
                else:
                    is_partial = True

            if is_accepted or is_partial:
                verified.append(ev)
                if is_partial:
                    marker = (
                        "EVIDENCE_LOW_TRANSCRIPTION_CONFIDENCE"
                        if low_asr_confidence
                        else "EVIDENCE_PARTIAL_SUPPORT"
                    )
                    _review(req, marker)
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated=(
                            "evidence_low_transcription_confidence"
                            if low_asr_confidence
                            else "evidence_semantic_mismatch"
                        ),
                        details=(
                            f"Evidence in chunk '{ev.chunk_id}' has low ASR confidence "
                            f"({float(asr_confidence):.2f})."
                            if low_asr_confidence
                            else (
                                f"Evidence in chunk '{ev.chunk_id}' has different "
                                "languages or ambiguous support."
                            )
                        ),
                    ))

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
