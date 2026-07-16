"""
Hybrid retrieval merge: BM25 (lexical) + pgvector (semantic).

Pure, deterministic merge logic (no I/O) so it is trivially testable. The node
supplies already-retrieved lexical hits and vector hits; this ranks them into one
list. Design invariants (spec §RAG PRODUCTION STRATEGY):

  * BM25 remains authoritative for exact/verbatim grounding.
  * Vector hits only augment recall — they carry the chunk's *own* text, so an
    attached snippet is always real source text, never model-invented.
  * A chunk found by both signals is boosted (agreement).
  * Scores are normalised so lexical and semantic contributions are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class MergedHit:
    chunk_id: str
    text: str
    score: float
    bm25_score: float = 0.0
    vector_score: float = 0.0
    page_number: Optional[int] = None
    speaker: Optional[str] = None
    timestamp: Optional[str] = None
    sources: List[str] = field(default_factory=list)


def _normalise(values: Dict[str, float]) -> Dict[str, float]:
    if not values:
        return {}
    top = max(values.values())
    if top <= 0:
        return {k: 0.0 for k in values}
    return {k: (v / top) for k, v in values.items()}


def merge_hits(
    bm25_hits: Sequence[Any],
    vector_hits: Sequence[Dict[str, Any]],
    chunks_by_id: Dict[str, Any],
    *,
    top_k: int = 3,
    bm25_weight: float = 0.5,
    vector_weight: float = 0.5,
    agreement_boost: float = 0.1,
) -> List[MergedHit]:
    """Merge + rank lexical and semantic hits into one list."""
    bm25_raw = {h.chunk_id: float(getattr(h, "score", 0.0)) for h in bm25_hits}
    vector_raw = {h["chunk_id"]: float(h.get("score", 0.0)) for h in vector_hits}
    bm25_norm = _normalise(bm25_raw)
    vector_norm = _normalise(vector_raw)

    merged: Dict[str, MergedHit] = {}

    def _meta(chunk_id: str, fallback: Any = None):
        chunk = chunks_by_id.get(chunk_id) or fallback
        text = getattr(chunk, "text", "") if chunk is not None else ""
        return (
            text,
            getattr(chunk, "page_number", None) if chunk is not None else None,
            getattr(chunk, "speaker", None) if chunk is not None else None,
            getattr(chunk, "timestamp", None) if chunk is not None else None,
        )

    for h in bm25_hits:
        cid = h.chunk_id
        text, page, speaker, ts = _meta(cid, h)
        merged[cid] = MergedHit(
            chunk_id=cid,
            text=text or getattr(h, "text", ""),
            score=bm25_weight * bm25_norm.get(cid, 0.0),
            bm25_score=round(bm25_raw.get(cid, 0.0), 6),
            page_number=page if page is not None else getattr(h, "page_number", None),
            speaker=speaker,
            timestamp=ts,
            sources=["bm25"],
        )

    for cid, raw in vector_raw.items():
        contribution = vector_weight * vector_norm.get(cid, 0.0)
        if cid in merged:
            merged[cid].score += contribution + agreement_boost
            merged[cid].vector_score = round(raw, 6)
            merged[cid].sources.append("vector")
        else:
            text, page, speaker, ts = _meta(cid)
            merged[cid] = MergedHit(
                chunk_id=cid,
                text=text,
                score=contribution,
                vector_score=round(raw, 6),
                page_number=page,
                speaker=speaker,
                timestamp=ts,
                sources=["vector"],
            )

    ranked = sorted(merged.values(), key=lambda m: m.score, reverse=True)
    return ranked[: max(0, top_k)]
