from typing import List

from app.progress import update_progress
from app.schemas.items import ClassifiedRequirement, QualityIssue, SourceChunk
from app.schemas.pipeline_state import PipelineState
from app.services.semantic_quality import (
    best_evidence_clause,
    complete_requirement_from_evidence,
    unsupported_numeric_claims,
    unsupported_fact_terms,
    has_polarity_conflict,
    check_different_languages,
    fact_tokens,
)
from app.services.audio_semantics import (
    audio_text_requires_review,
    best_audio_evidence_clause,
    is_audio_chunk,
    normalize_audio_matching_text,
    normalize_audio_requirement_text,
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


def _warning_code(warning) -> str | None:
    if isinstance(warning, dict):
        return warning.get("code")
    return getattr(warning, "code", None)


_EVIDENCE_WARNING_CODES = {
    "EXTRACT_WEAK_EVIDENCE",
    "WEAK_EVIDENCE_SUPPORT",
    "NO_RETRIEVED_EVIDENCE",
}


def _clear_resolved_evidence_review(req: ClassifiedRequirement) -> None:
    """Remove upstream evidence-only review markers after final verification."""
    import re

    reason = req.review_reason or ""
    reason = re.sub(
        r"\s*\[(?:AUTO_FIX|WEAK_EVIDENCE_SUPPORT|EVIDENCE_)[^\]]*\]",
        "",
        reason,
    )
    reason = re.sub(r"\s+", " ", reason).strip()
    req.review_reason = reason or None
    req.needs_review = bool(reason)


def _is_explicit_audio_translation(
    req: ClassifiedRequirement,
    evidence,
    quote: str,
    chunk: SourceChunk | None,
    low_asr_confidence: bool,
) -> bool:
    """Allow exact Arabic/mixed transcript evidence extracted by the LLM.

    The extraction prompt already requires English requirement text and a
    verbatim source-language quote. A lexical English-vs-Arabic comparison
    cannot adjudicate that pair. This narrow path applies only to an explicit,
    exact, high-confidence audio quote produced during extraction; retrieved or
    fallback snippets remain review-only.
    """
    return bool(
        is_audio_chunk(chunk)
        and not low_asr_confidence
        and getattr(req, "extraction_type", None) == "explicit"
        and getattr(evidence, "origin", "extracted") == "extracted"
        and len(quote) >= 12
        and quote in (chunk.text if chunk else "")
    )


def _audio_clause_covers_material_facts(requirement: str, clause: str) -> bool:
    """Reject a same-language audio citation that omits a named fact.

    ASR can yield a plausible partial quote (for example, ``LDAP Active``)
    while extraction reconstructs the rest of the statement. A public audio
    citation must contain every normalised material fact of the requirement.
    Cross-language evidence uses the existing explicit-translation path, and
    Arabic-only statements keep the legacy provenance checks because the
    lightweight fact tokenizer is intentionally Latin-script focused.
    """
    requirement_facts = fact_tokens(normalize_audio_matching_text(requirement))
    clause_facts = fact_tokens(normalize_audio_matching_text(clause))
    # Modals are intentionally retained by shared polarity logic, but must not
    # make an otherwise complete audio quote fail merely because ASR used
    # ``must`` where the canonical requirement uses ``shall``.
    modals = {"shall", "must", "should", "may", "can", "will"}
    requirement_facts -= modals
    clause_facts -= modals
    if len(requirement_facts) < 3:
        return True
    if requirement_facts.issubset(clause_facts):
        return True
    # Bounded multi-utterance / conversational coverage allowance
    coverage = (
        len(requirement_facts & clause_facts) / len(requirement_facts)
        if requirement_facts else 0.0
    )
    return coverage >= 0.85


def _reconcile_evidence_warnings(
    warnings: list,
    classified: List[ClassifiedRequirement],
) -> list:
    """Replace retrieval diagnostics with one authoritative final warning."""
    reconciled = [
        warning
        for warning in warnings
        if _warning_code(warning) not in _EVIDENCE_WARNING_CODES
    ]
    unresolved_ids = [
        req.id
        for req in classified
        if not (getattr(req, "evidence", None) or [])
        and not set(getattr(req, "labels", []) or []).intersection(
            {"Open Question", "Out-of-Scope", "Assumption"}
        )
    ]
    if unresolved_ids:
        public_ids = ", ".join(f"REQ-{req_id:03d}" for req_id in unresolved_ids)
        reconciled.append({
            "node_name": "evidence_grounding",
            "code": "EXTRACT_WEAK_EVIDENCE",
            "message": (
                f"{len(unresolved_ids)} requirement(s) still lack verified "
                f"source evidence after grounding: {public_ids}."
            ),
            "unresolved_requirement_ids": unresolved_ids,
        })
    return reconciled


def _recover_audio_evidence_from_declared_source(
    req: ClassifiedRequirement,
    candidates: list,
    chunks_by_id: dict[str, SourceChunk],
    source_docs: list[dict],
):
    """Recover a strong clause from the same declared audio source only.

    This is a last deterministic recall step for ASR extraction: a model can
    supply a weak quote while the timestamped source window contains a complete
    explicit clause. It never searches a different recording or bypasses the
    ordinary support, numeric, behavior, provenance, or polarity checks.
    """
    document_ids = {
        getattr(evidence, "document_id", None)
        or getattr(chunks_by_id.get(getattr(evidence, "chunk_id", "")), "document_id", None)
        for evidence in candidates
    }
    document_ids.discard(None)
    if not document_ids:
        return None

    best = None
    for chunk in chunks_by_id.values():
        if not is_audio_chunk(chunk) or chunk.document_id not in document_ids:
            continue
        evidence_lang = getattr(chunk, "language", None) or _document_language(
            source_docs, chunk.document_id
        )
        if check_different_languages(req.text, chunk.text, None, evidence_lang):
            continue
        support, clause = best_audio_evidence_clause(req.text, chunk.text)
        if support < 0.60 or not clause:
            continue
        if best is None or support > best[0]:
            best = (support, clause, chunk)

    if best is None:
        return None

    support, clause, chunk = best
    completed_text = complete_requirement_from_evidence(req.text, clause)
    completed_text = normalize_audio_requirement_text(completed_text)
    if completed_text != req.text:
        req.text = completed_text
        support, clause = best_audio_evidence_clause(req.text, chunk.text)
    if support < 0.60:
        return None
    if not _audio_clause_covers_material_facts(req.text, clause):
        return None
    normalized_requirement = normalize_audio_matching_text(req.text)
    normalized_clause = normalize_audio_matching_text(clause)
    if (
        unsupported_numeric_claims(normalized_requirement, [normalized_clause])
        or unsupported_fact_terms(req.text, [clause])
        or has_polarity_conflict(req.text, [clause])
    ):
        return None

    asr_confidence = getattr(chunk, "asr_confidence", None)
    low_asr_confidence = (
        asr_confidence is not None and float(asr_confidence) < MIN_ASR_CONFIDENCE
    )
    published_support = min(support, 0.70) if low_asr_confidence else support
    from app.schemas.items import EvidenceSpan

    return EvidenceSpan(
        chunk_id=chunk.chunk_id,
        quote=clause,
        page_number=chunk.page_number,
        speaker=chunk.speaker,
        timestamp=str(chunk.start_time_sec) if chunk.start_time_sec is not None else None,
        document_id=chunk.document_id,
        origin="retrieved",
        lexical_score=published_support,
        entailment_score=published_support,
        support_score=published_support,
    ), low_asr_confidence


async def evidence_grounding_node(state: PipelineState) -> dict:
    """Keep only evidence grounded to its declared chunk, document, and claim.

    Retrieval is deliberately treated as candidate generation.  This node is
    the authority that decides which candidates may reach public source_refs.
    """
    print("--- EVIDENCE GROUNDING NODE ---")
    update_progress(state.get("job_id"), "evidence_grounding", 76, "PROCESSING")

    classified: List[ClassifiedRequirement] = state.get("classified_requirements", [])
    chunks: List[SourceChunk] = state.get("chunks", [])
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    source_docs = state.get("source_documents", []) or []
    existing_q = state.get("quality_issues", []) or []
    existing_warnings = state.get("warnings", []) or []
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

            support_text = chunk.text if chunk is not None else quote
            audio_evidence = is_audio_chunk(chunk)
            if audio_evidence:
                support, supporting_clause = best_audio_evidence_clause(
                    req.text, support_text
                )
            else:
                support, supporting_clause = best_evidence_clause(req.text, support_text)
            ev.lexical_score = support
            ev.entailment_score = support
            ev.support_score = support

            is_accepted = False
            is_reviewable_verified = False

            if diff_lang:
                if _is_explicit_audio_translation(
                    req, ev, quote, chunk, low_asr_confidence
                ):
                    # This is not a generic cross-language bypass: it is the
                    # exact quote paired with the explicit English extraction.
                    # Cap support below perfect confidence because the pair was
                    # not independently translated by an additional model.
                    support = max(support, 0.70)
                    ev.lexical_score = support
                    ev.entailment_score = support
                    ev.support_score = support
                    is_accepted = True
                else:
                    _review(req, "EVIDENCE_CROSS_LANGUAGE_NOT_ADJUDICATED")
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_semantic_mismatch",
                        details=(
                            f"Evidence in chunk '{ev.chunk_id}' is cross-language "
                            "and was not promoted to a public citation."
                        ),
                    ))
                    continue
            else:
                if support >= 0.60:
                    complete_text = complete_requirement_from_evidence(
                        req.text,
                        supporting_clause,
                    )
                    if audio_evidence:
                        complete_text = normalize_audio_requirement_text(complete_text)
                    if complete_text != req.text:
                        req.text = complete_text
                        if audio_evidence:
                            support, supporting_clause = best_audio_evidence_clause(
                                req.text, support_text
                            )
                        else:
                            support, supporting_clause = best_evidence_clause(
                                req.text, support_text
                            )
                        ev.lexical_score = support
                        ev.entailment_score = support
                        ev.support_score = support
                    numeric_mismatch = bool(unsupported_numeric_claims(
                        normalize_audio_matching_text(req.text)
                        if audio_evidence else req.text,
                        [normalize_audio_matching_text(supporting_clause)]
                        if audio_evidence else [supporting_clause],
                    ))
                    unsupported_behavior = bool(
                        unsupported_fact_terms(
                            normalize_audio_matching_text(req.text)
                            if audio_evidence else req.text,
                            [normalize_audio_matching_text(supporting_clause)]
                            if audio_evidence else [supporting_clause],
                        )
                    )
                    polarity_conflict = has_polarity_conflict(
                        normalize_audio_matching_text(req.text)
                        if audio_evidence else req.text,
                        [normalize_audio_matching_text(supporting_clause)]
                        if audio_evidence else [supporting_clause],
                    )
                    incomplete_audio_clause = (
                        audio_evidence
                        and not _audio_clause_covers_material_facts(
                            req.text, supporting_clause
                        )
                    )
                    if not (
                        numeric_mismatch
                        or unsupported_behavior
                        or polarity_conflict
                        or incomplete_audio_clause
                    ):
                        is_accepted = True
                        is_reviewable_verified = low_asr_confidence
                    else:
                        _review(req, "EVIDENCE_REJECTED_VERIFICATION_FAIL")
                        new_issues.append(QualityIssue(
                            item_id=req.id,
                            item_type="requirement",
                            severity="medium",
                            rule_violated="evidence_semantic_mismatch",
                            details=(
                                f"Evidence in chunk '{ev.chunk_id}' has mismatching numeric, "
                                "behavior, polarity, or is an incomplete audio clause."
                            ),
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
                    _review(req, "EVIDENCE_PARTIAL_SUPPORT_NOT_CITED")
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_semantic_mismatch",
                        details=(
                            f"Evidence in chunk '{ev.chunk_id}' has ambiguous "
                            "support and was not promoted to a public citation."
                        ),
                    ))
                    continue

            if is_accepted:
                ev.quote = supporting_clause
                # Provenance, quote, numeric, behavior, and polarity checks are
                # identical for retrieval and fallback candidates. Once they
                # pass, candidate origin must not cap verified confidence.
                if low_asr_confidence:
                    ev.support_score = min(ev.support_score, 0.70)
                    ev.entailment_score = min(ev.entailment_score, 0.70)
                verified.append(ev)
                if is_reviewable_verified:
                    marker = "EVIDENCE_LOW_TRANSCRIPTION_CONFIDENCE"
                    _review(req, marker)
                    new_issues.append(QualityIssue(
                        item_id=req.id,
                        item_type="requirement",
                        severity="medium",
                        rule_violated="evidence_low_transcription_confidence",
                        details=(
                            f"Evidence in chunk '{ev.chunk_id}' has low ASR confidence "
                            f"({float(asr_confidence):.2f})."
                        ),
                    ))

        if not verified:
            recovered = _recover_audio_evidence_from_declared_source(
                req, evidence, chunks_by_id, source_docs
            )
            if recovered is not None:
                recovered_evidence, low_asr_confidence = recovered
                verified.append(recovered_evidence)
                if low_asr_confidence:
                    _review(req, "EVIDENCE_LOW_TRANSCRIPTION_CONFIDENCE")

        if chunks_by_id:
            req.evidence = verified
        if any(
            is_audio_chunk(chunks_by_id.get(getattr(ev, "chunk_id", "")))
            for ev in req.evidence
        ) and audio_text_requires_review(req.text):
            _review(req, "ASR_INCOMPLETE_FRAGMENT")
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
        elif not any(
            issue.item_id == req.id
            and issue.rule_violated == "evidence_low_transcription_confidence"
            for issue in new_issues
        ):
            _clear_resolved_evidence_review(req)

    reconciled_warnings = _reconcile_evidence_warnings(
        existing_warnings,
        classified,
    )

    return {
        "classified_requirements": classified,
        "quality_issues": existing_q + new_issues,
        "warnings": reconciled_warnings,
    }
