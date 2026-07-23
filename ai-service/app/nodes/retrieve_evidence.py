"""
retrieve_evidence node.

Uses the per-job lexical source index (Phase 2) to strengthen each requirement's
traceability *before* classification:

  * Build a query from the requirement text + actor + goal.
  * Retrieve the top supporting chunks.
  * Attach the best supporting snippet as additional evidence — but only from
    chunks not already cited, and capped, so the final output is not bloated by
    full-chunk dumps.
  * Keep all original evidence.
  * Record retrieval scores (evidence_match_score, quote_support_score) and lower
    confidence / warn when support is weak.

This is source grounding: retrieval attaches *evidence*, it never rewrites the
requirement text.
"""

from __future__ import annotations

import re
from typing import List

import logging

from app.progress import update_progress
from app.rag.scoring import tokenize
from app.rag.source_index import get_source_index
from app.schemas.items import EvidenceSpan, ExtractedRequirement, PipelineWarning, SourceChunk
from app.schemas.pipeline_state import PipelineState
from app.services.semantic_quality import (
    lexical_support,
    unsupported_numeric_claims,
    unsupported_fact_terms,
    has_polarity_conflict,
    check_different_languages,
)

logger = logging.getLogger("app.nodes.retrieve_evidence")

RETRIEVE_TOP_K = 3
MAX_EVIDENCE_PER_REQ = 4
SNIPPET_MAX_CHARS = 240
WEAK_CONFIDENCE_FACTOR = 0.85
MIN_RETRIEVAL_SUPPORT = 0.35
MIN_ASR_CONFIDENCE = 0.65


def _build_query(req: ExtractedRequirement) -> str:
    parts = [req.text or ""]
    if req.actor:
        parts.append(str(req.actor))
    if req.goal:
        parts.append(str(req.goal))
    return " ".join(p for p in parts if p).strip()


def _best_snippet(text: str, query_tokens: set, max_len: int = SNIPPET_MAX_CHARS) -> str:
    """Pick the sentence in ``text`` with the most query-term overlap."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if s.strip()]
    if not sentences:
        return text[:max_len].strip()
    best = max(sentences, key=lambda s: len(query_tokens & set(tokenize(s))))
    return best[:max_len].strip()


def _document_language(source_docs: List[dict] | None, document_id: str | None) -> str | None:
    if not source_docs or not document_id:
        return None
    for doc in source_docs:
        if (doc.get("document_id") or doc.get("source_id")) == document_id:
            return doc.get("language")
    return None


def _quote_support_score(
    req: ExtractedRequirement,
    chunks_by_id: dict[str, SourceChunk],
    source_docs: List[dict] | None = None,
) -> float:
    """Score the strongest quote grounded in its declared chunk and document."""
    scores: List[float] = []
    for evidence in req.evidence:
        quote = (evidence.quote or "").strip()
        chunk = chunks_by_id.get(evidence.chunk_id)
        if not quote or chunk is None or quote not in chunk.text:
            evidence.support_score = 0.0
            continue
        if evidence.document_id and evidence.document_id != chunk.document_id:
            evidence.support_score = 0.0
            continue

        doc_id = evidence.document_id or chunk.document_id
        evidence_lang = getattr(chunk, "language", None) or _document_language(
            source_docs,
            doc_id,
        )
        diff_lang = check_different_languages(req.text, quote, None, evidence_lang)
        asr_confidence = getattr(chunk, "asr_confidence", None)
        low_asr_confidence = (
            asr_confidence is not None
            and float(asr_confidence) < MIN_ASR_CONFIDENCE
        )

        score = lexical_support(req.text, quote)
        if evidence.origin == "fallback":
            # A source snippet can still strongly support the claim even when
            # verbatim alignment failed because punctuation/layout changed.
            # It is capped so it can never look as strong as an exact quote.
            score = min(score, 0.70)

        is_partial = False

        if diff_lang:
            is_partial = True
        else:
            if score >= 0.60:
                numeric_mismatch = bool(unsupported_numeric_claims(req.text, [quote]))
                unsupported_behavior = bool(unsupported_fact_terms(req.text, [quote]))
                polarity_conflict = has_polarity_conflict(req.text, [quote])
                if numeric_mismatch or unsupported_behavior or polarity_conflict:
                    evidence.support_score = 0.0
                    continue
                is_partial = low_asr_confidence
            elif score < 0.25:
                evidence.support_score = 0.0
                continue
            else:
                is_partial = True

        evidence.lexical_score = score
        evidence.entailment_score = score
        evidence.support_score = score
        scores.append(score)

        if is_partial:
            req.needs_review = True
            if low_asr_confidence:
                reason = "[WEAK_EVIDENCE_SUPPORT: low ASR confidence]"
            elif diff_lang:
                reason = "[WEAK_EVIDENCE_SUPPORT: different languages]"
            else:
                reason = "[WEAK_EVIDENCE_SUPPORT: ambiguous/partial support]"
            if reason not in (req.review_reason or ""):
                req.review_reason = ((req.review_reason or "") + " " + reason).strip()

    return round(max(scores, default=0.0), 4)


async def retrieve_evidence_node(state: PipelineState) -> dict:
    print("--- RETRIEVE EVIDENCE NODE ---")
    job_id = state.get("job_id") or ""
    update_progress(job_id, "retrieve_evidence", 60, "PROCESSING")

    reqs: List[ExtractedRequirement] = state.get("extracted_requirements", []) or []
    if not reqs:
        return {}

    retriever = get_source_index(state.get("source_index_id") or job_id)
    chunks: List[SourceChunk] = state.get("chunks", []) or []
    chunks_by_id = {c.chunk_id: c for c in chunks}
    source_docs = state.get("source_documents", []) or []

    if retriever is None or retriever.size == 0:
        # No index to retrieve from — still record quote support so quality can act.
        for req in reqs:
            req.quote_support_score = _quote_support_score(req, chunks_by_id, source_docs)
        warning = PipelineWarning(
            node_name="retrieve_evidence",
            code="NO_RETRIEVED_EVIDENCE",
            message="No source index available; evidence retrieval was skipped.",
        )
        return {
            "extracted_requirements": reqs,
            "warnings": (state.get("warnings", []) or []) + [warning],
        }

    # --- Optional hybrid (BM25 + pgvector) setup --------------------------
    # Opt-in per job. BM25 stays authoritative for exact grounding; vector
    # search only augments recall and is scoped by tenant/project/job so it can
    # never surface another tenant's or project's chunks. Any failure degrades
    # cleanly back to lexical-only retrieval.
    hybrid = bool(state.get("enable_hybrid_retrieval"))
    embedder = None
    embedding_store = None
    if hybrid:
        try:
            from app.rag.embeddings import get_embedder
            from app.store.factory import get_stores

            embedder = get_embedder()
            embedding_store = get_stores().embeddings
            hybrid = embedder is not None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("hybrid retrieval disabled (setup failed): %s", type(exc).__name__)
            hybrid = False

    weak_support = 0
    no_hits = 0
    limit_applied = 0
    vector_used = 0

    for req in reqs:
        query = _build_query(req)
        query_tokens = set(tokenize(query))
        bm25_hits = retriever.retrieve(query, top_k=RETRIEVE_TOP_K)

        req.evidence_match_score = 0.0
        req.quote_support_score = _quote_support_score(req, chunks_by_id, source_docs)

        hits = bm25_hits
        if hybrid:
            try:
                from app.rag.hybrid import merge_hits

                q_emb = await embedder.embed_query(query)
                v_hits = await embedding_store.vector_search(
                    q_emb,
                    tenant_id=state.get("tenant_id"),
                    project_id=state.get("project_id"),
                    job_id=job_id,
                    top_k=RETRIEVE_TOP_K,
                )
                merged = merge_hits(bm25_hits, v_hits, chunks_by_id, top_k=RETRIEVE_TOP_K)
                if merged:
                    hits = merged
                    req.vector_match_score = round(
                        max((h.vector_score for h in merged), default=0.0), 4
                    )
                    if any("vector" in h.sources for h in merged):
                        vector_used += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("hybrid retrieval failed for req %s: %s", req.id, type(exc).__name__)

        # Retrieval results are candidates, not citations.  Only candidates
        # that support the requirement proposition become evidence.
        cited = {e.chunk_id for e in req.evidence}
        qualified_hits = 0
        for hit in hits:
            if len(req.evidence) >= MAX_EVIDENCE_PER_REQ:
                limit_applied += 1
                break
            if hit.chunk_id in cited:
                continue
            snippet = _best_snippet(hit.text, query_tokens)
            if not snippet:
                continue

            support = lexical_support(req.text, snippet)
            orig_chunk = chunks_by_id.get(hit.chunk_id)
            orig_doc_id = getattr(orig_chunk, "document_id", None) if orig_chunk else None

            evidence_lang = (
                getattr(orig_chunk, "language", None)
                if orig_chunk is not None
                else None
            ) or _document_language(source_docs, orig_doc_id)
            diff_lang = check_different_languages(
                req.text,
                snippet,
                None,
                evidence_lang,
            )
            asr_confidence = (
                getattr(orig_chunk, "asr_confidence", None)
                if orig_chunk is not None
                else None
            )
            low_asr_confidence = (
                asr_confidence is not None
                and float(asr_confidence) < MIN_ASR_CONFIDENCE
            )

            is_accepted = False
            is_partial = False

            if diff_lang:
                # A cross-language retrieval hit cannot be adjudicated by the
                # deterministic lexical guard.  Do not promote it into a
                # citation; only already-extracted evidence may be retained for
                # review by the authoritative grounding node.
                req.needs_review = True
                reason = "[WEAK_EVIDENCE_SUPPORT: cross-language retrieval not promoted]"
                if reason not in (req.review_reason or ""):
                    req.review_reason = ((req.review_reason or "") + " " + reason).strip()
                continue
            else:
                if support >= 0.60:
                    numeric_mismatch = bool(unsupported_numeric_claims(req.text, [snippet]))
                    unsupported_behavior = bool(unsupported_fact_terms(req.text, [snippet]))
                    polarity_conflict = has_polarity_conflict(req.text, [snippet])
                    if not (numeric_mismatch or unsupported_behavior or polarity_conflict):
                        is_partial = low_asr_confidence
                        is_accepted = not low_asr_confidence
                    else:
                        continue
                elif support < 0.25:
                    continue
                else:
                    is_partial = True

            if is_accepted or is_partial:
                req.evidence.append(EvidenceSpan(
                    chunk_id=hit.chunk_id,
                    quote=snippet,
                    page_number=hit.page_number,
                    speaker=hit.speaker,
                    timestamp=hit.timestamp,
                    document_id=orig_doc_id,
                    origin="retrieved",
                    lexical_score=support,
                    entailment_score=support,
                    support_score=support,
                ))
                cited.add(hit.chunk_id)
                qualified_hits += 1
                req.evidence_match_score = max(req.evidence_match_score or 0.0, support)

                if is_partial:
                    req.needs_review = True
                    if low_asr_confidence:
                        reason = "[WEAK_EVIDENCE_SUPPORT: low ASR confidence]"
                    elif diff_lang:
                        reason = "[WEAK_EVIDENCE_SUPPORT: different languages]"
                    else:
                        reason = "[WEAK_EVIDENCE_SUPPORT: ambiguous/partial support]"
                    if reason not in (req.review_reason or ""):
                        req.review_reason = ((req.review_reason or "") + " " + reason).strip()

        if not qualified_hits and req.quote_support_score < MIN_RETRIEVAL_SUPPORT:
            no_hits += 1

        req.quote_support_score = max(
            req.quote_support_score,
            max((e.support_score for e in req.evidence), default=0.0),
        )

        # Weak support: no grounded quote AND no relevant lexical/semantic match.
        if (
            req.quote_support_score < MIN_RETRIEVAL_SUPPORT
            and (req.evidence_match_score or 0.0) < MIN_RETRIEVAL_SUPPORT
        ):
            weak_support += 1
            req.needs_review = True
            req.review_reason = (req.review_reason or "") + " [WEAK_EVIDENCE_SUPPORT: no grounded quote and no relevant source match]"
            try:
                req.confidence = round(max(0.0, float(req.confidence) * WEAK_CONFIDENCE_FACTOR), 4)
            except (TypeError, ValueError):
                req.confidence = round(0.5 * WEAK_CONFIDENCE_FACTOR, 4)

    result: dict = {"extracted_requirements": reqs}

    # Record retrieval mode + hybrid stats alongside the index stats.
    existing_stats = state.get("retrieval_stats") or {}
    result["retrieval_stats"] = {
        **existing_stats,
        "mode": "hybrid" if hybrid else "lexical",
        "requirements_with_vector_support": vector_used,
    }

    new_warnings: List[PipelineWarning] = []
    if weak_support:
        new_warnings.append(PipelineWarning(
            node_name="retrieve_evidence",
            code="WEAK_EVIDENCE_SUPPORT",
            message=f"{weak_support} requirement(s) have weak source support and were flagged for review.",
        ))
    if no_hits:
        new_warnings.append(PipelineWarning(
            node_name="retrieve_evidence",
            code="NO_RETRIEVED_EVIDENCE",
            message=f"{no_hits} requirement(s) returned no supporting chunks from retrieval.",
        ))
    if limit_applied:
        new_warnings.append(PipelineWarning(
            node_name="retrieve_evidence",
            code="EVIDENCE_LIMIT_APPLIED",
            message=f"Evidence capped at {MAX_EVIDENCE_PER_REQ} per requirement for {limit_applied} requirement(s).",
        ))
    if new_warnings:
        result["warnings"] = (state.get("warnings", []) or []) + new_warnings

    return result
