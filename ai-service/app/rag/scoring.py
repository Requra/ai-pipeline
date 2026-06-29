"""
Deterministic lexical scoring primitives for source grounding.

Implements a small, dependency-free BM25 variant. The IDF term uses the
``log(1 + (N - df + 0.5)/(df + 0.5))`` form which stays strictly positive even
on tiny corpora (a single chunk), so retrieval still ranks sensibly when a short
document produces only one or two chunks.

Everything here is pure and deterministic: same input → same scores → same order.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Sequence

# Minimal English stopword set — kept small on purpose so domain terms like
# "system", "user", "data" still carry signal for requirement retrieval.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
        "he", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
        "was", "were", "will", "with", "this", "these", "those", "but", "not",
        "we", "you", "they", "their", "our", "i", "so", "if", "then", "than",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# BM25 tuning constants (standard defaults).
BM25_K1 = 1.5
BM25_B = 0.75


def tokenize(text: str) -> List[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and 1-char tokens.

    Returns an empty list for falsy/whitespace input (never raises).
    """
    if not text:
        return []
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) > 1 and tok not in _STOPWORDS
    ]


@dataclass(frozen=True)
class CorpusStats:
    """Precomputed corpus-level statistics for BM25 scoring."""

    idf: Dict[str, float]
    avg_doc_len: float
    doc_count: int


def build_corpus_stats(doc_token_lists: Sequence[Sequence[str]]) -> CorpusStats:
    """Compute IDF and average document length across tokenized documents."""
    doc_count = len(doc_token_lists)
    if doc_count == 0:
        return CorpusStats(idf={}, avg_doc_len=0.0, doc_count=0)

    doc_freq: Dict[str, int] = {}
    total_len = 0
    for tokens in doc_token_lists:
        total_len += len(tokens)
        for term in set(tokens):
            doc_freq[term] = doc_freq.get(term, 0) + 1

    idf: Dict[str, float] = {}
    for term, df in doc_freq.items():
        # Always-positive BM25+ idf variant.
        idf[term] = math.log(1.0 + (doc_count - df + 0.5) / (df + 0.5))

    avg_doc_len = total_len / doc_count if doc_count else 0.0
    return CorpusStats(idf=idf, avg_doc_len=avg_doc_len, doc_count=doc_count)


def bm25_score(
    query_tokens: Sequence[str],
    doc_tokens: Sequence[str],
    stats: CorpusStats,
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    """Return the BM25 relevance score of ``doc_tokens`` for ``query_tokens``.

    Returns 0.0 when either side is empty or no query term occurs in the doc.
    """
    if not query_tokens or not doc_tokens:
        return 0.0

    doc_len = len(doc_tokens)
    avg_len = stats.avg_doc_len or float(doc_len) or 1.0

    # Term frequencies in the document.
    tf: Dict[str, int] = {}
    for term in doc_tokens:
        tf[term] = tf.get(term, 0) + 1

    score = 0.0
    for term in query_tokens:
        term_tf = tf.get(term)
        if not term_tf:
            continue
        idf = stats.idf.get(term)
        if idf is None:
            # Unseen-in-corpus query term: treat with a small positive idf so an
            # exact lexical match still contributes (avoids dropping signal).
            idf = math.log(1.0 + (stats.doc_count + 0.5) / 0.5) if stats.doc_count else 1.0
        denom = term_tf + k1 * (1.0 - b + b * (doc_len / avg_len))
        score += idf * (term_tf * (k1 + 1.0)) / denom
    return score
