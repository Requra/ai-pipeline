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

logger = logging.getLogger("app.nodes.retrieve_evidence")

RETRIEVE_TOP_K = 3
MAX_EVIDENCE_PER_REQ = 4
SNIPPET_MAX_CHARS = 240
WEAK_CONFIDENCE_FACTOR = 0.85


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


def _quote_support_score(req: ExtractedRequirement, chunk_texts: List[str]) -> float:
    """Fraction of the requirement's evidence quotes found verbatim in source."""
    quotes = [e.quote for e in req.evidence if (e.quote or "").strip()]
    if not quotes:
        return 0.0
    found = sum(1 for q in quotes if any(q in ct for ct in chunk_texts))
    return round(found / len(quotes), 4)


async def retrieve_evidence_node(state: PipelineState) -> dict:
    print("--- RETRIEVE EVIDENCE NODE ---")
    job_id = state.get("job_id") or ""
    update_progress(job_id, "retrieve_evidence", 60, "PROCESSING")

    reqs: List[ExtractedRequirement] = state.get("extracted_requirements", []) or []
    if not reqs:
        return {}

    retriever = get_source_index(state.get("source_index_id") or job_id)
    chunks: List[SourceChunk] = state.get("chunks", []) or []
    chunk_texts = [c.text for c in chunks]

    if retriever is None or retriever.size == 0:
        # No index to retrieve from — still record quote support so quality can act.
        for req in reqs:
            req.quote_support_score = _quote_support_score(req, chunk_texts)
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
    chunks_by_id = {c.chunk_id: c for c in chunks}
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

        req.evidence_match_score = round(bm25_hits[0].score, 4) if bm25_hits else 0.0
        req.quote_support_score = _quote_support_score(req, chunk_texts)

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

        if not hits:
            no_hits += 1

        # Attach supporting evidence from NEW chunks only, capped.
        cited = {e.chunk_id for e in req.evidence}
        for hit in hits:
            if len(req.evidence) >= MAX_EVIDENCE_PER_REQ:
                limit_applied += 1
                break
            if hit.chunk_id in cited:
                continue
            snippet = _best_snippet(hit.text, query_tokens)
            if not snippet:
                continue
            
            orig_chunk = chunks_by_id.get(hit.chunk_id)
            orig_doc_id = getattr(orig_chunk, "document_id", None) if orig_chunk else None

            req.evidence.append(EvidenceSpan(
                chunk_id=hit.chunk_id,
                quote=snippet,
                page_number=hit.page_number,
                speaker=hit.speaker,
                timestamp=hit.timestamp,
                document_id=orig_doc_id,
            ))
            cited.add(hit.chunk_id)

        # Weak support: no grounded quote AND no relevant lexical/semantic match.
        if (
            req.quote_support_score == 0.0
            and req.evidence_match_score == 0.0
            and (req.vector_match_score or 0.0) == 0.0
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
