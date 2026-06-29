"""
In-memory lexical retriever over a set of ``SourceChunk`` objects.

Deterministic BM25 ranking. Builds once per document/job, then answers many
top-k queries (one per requirement during evidence retrieval). No external
services, no embeddings — by design for the MVP.
"""

from __future__ import annotations

from typing import List, Sequence

from app.rag.scoring import CorpusStats, bm25_score, build_corpus_stats, tokenize
from app.schemas.items import RetrievedChunk, SourceChunk


class LexicalRetriever:
    """BM25 retriever built from a list of :class:`SourceChunk`.

    Robust to empty input: an empty chunk set yields an empty retriever whose
    :meth:`retrieve` always returns ``[]``.
    """

    def __init__(self, chunks: Sequence[SourceChunk]) -> None:
        # Keep only chunks that have usable text; preserve original order so
        # ties break deterministically by document position.
        self._chunks: List[SourceChunk] = [
            c for c in (chunks or []) if getattr(c, "text", "").strip()
        ]
        self._doc_tokens: List[List[str]] = [tokenize(c.text) for c in self._chunks]
        self._stats: CorpusStats = build_corpus_stats(self._doc_tokens)

    @property
    def size(self) -> int:
        return len(self._chunks)

    @property
    def stats(self) -> CorpusStats:
        return self._stats

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[RetrievedChunk]:
        """Return up to ``top_k`` chunks scored for ``query``, highest first.

        * Empty query or empty index → ``[]``.
        * Results with ``score <= min_score`` are dropped.
        * Stable ordering: ties keep document order (lower index first).
        """
        if top_k <= 0 or not self._chunks:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored: List[tuple[int, float]] = []
        for idx, doc_tokens in enumerate(self._doc_tokens):
            score = bm25_score(query_tokens, doc_tokens, self._stats)
            if score > min_score:
                scored.append((idx, score))

        # Sort by score desc, then original index asc (stable, deterministic).
        scored.sort(key=lambda pair: (-pair[1], pair[0]))

        results: List[RetrievedChunk] = []
        for idx, score in scored[:top_k]:
            chunk = self._chunks[idx]
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    score=round(float(score), 6),
                    page_number=chunk.page_number,
                    start_char=chunk.start_char,
                    end_char=chunk.end_char,
                    speaker=chunk.speaker,
                    timestamp=(
                        str(chunk.start_time_sec)
                        if chunk.start_time_sec is not None
                        else None
                    ),
                )
            )
        return results
