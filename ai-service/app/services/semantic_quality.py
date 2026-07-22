"""Deterministic semantic-quality helpers used by existing pipeline nodes.

The functions here deliberately avoid provider calls.  They are a conservative
first line of defence against unrelated retrieval hits, incorrect story links,
and acceptance criteria that introduce unsupported numeric facts.  The public
API contract is unchanged; callers store the derived scores only on internal
models.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from app.rag.scoring import tokenize


_GENERIC_TERMS = {
    "system", "service", "application", "portal", "screen", "user", "users",
    "shall", "must", "should", "want", "wants", "allow", "allows", "able",
    "given", "when", "then", "using", "perform", "action", "result", "clear",
}
_NUMBER_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?|%|percent|mb|gb|kb)?\b",
    re.IGNORECASE,
)


def meaningful_tokens(text: str) -> set[str]:
    """Return domain-bearing tokens, excluding generic requirement language."""
    return {token for token in tokenize(text or "") if token not in _GENERIC_TERMS}


def normalized_numbers(text: str) -> set[str]:
    """Return normalized numeric claims including their units when present."""
    return {re.sub(r"\s+", " ", match.group(0).strip().lower()) for match in _NUMBER_RE.finditer(text or "")}


def lexical_support(requirement: str, evidence: str) -> float:
    """Score whether evidence supports the proposition in ``requirement``.

    Recall is weighted more heavily than precision: a short verbatim quote can
    be valid evidence even when it omits surrounding prose, but an unrelated
    quote sharing only words such as "system" or "user" receives no credit.
    Numeric incompatibility is a hard rejection because invented limits are a
    particularly damaging form of requirements hallucination.
    """
    req_tokens = meaningful_tokens(requirement)
    ev_tokens = meaningful_tokens(evidence)
    if not req_tokens or not ev_tokens:
        return 0.0

    req_numbers = normalized_numbers(requirement)
    ev_numbers = normalized_numbers(evidence)
    if ev_numbers and req_numbers and not ev_numbers.issubset(req_numbers):
        return 0.0

    overlap = req_tokens & ev_tokens
    recall = len(overlap) / len(req_tokens)
    precision = len(overlap) / len(ev_tokens)
    score = (0.75 * recall) + (0.25 * precision)
    return round(max(0.0, min(1.0, score)), 4)


def story_alignment(requirement_texts: Sequence[str], story_text: str) -> float:
    """Return the best lexical alignment between a story and its linked requirements."""
    if not requirement_texts:
        return 0.0
    return max((lexical_support(text, story_text) for text in requirement_texts), default=0.0)


def is_substantive(text: str) -> bool:
    """Whether text contains enough information for a semantic comparison."""
    return len(meaningful_tokens(text)) >= 3


def unsupported_numeric_claims(text: str, sources: Iterable[str]) -> set[str]:
    """Find numbers/units introduced by text but absent from all source facts."""
    claims = normalized_numbers(text)
    if not claims:
        return set()
    supported: set[str] = set()
    for source in sources:
        supported.update(normalized_numbers(source))
    return claims - supported
