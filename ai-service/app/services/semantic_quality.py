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

_FACT_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FACT_SCAFFOLDING = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "given",
    "has", "have", "i", "if", "in", "is", "it", "its", "of", "on",
    "or", "so", "that", "the", "their", "them", "then", "they", "this",
    "to", "user", "users", "when", "will", "with", "within", "want",
    "wants", "system", "service", "application", "portal", "screen",
    "clearly", "successfully", "relevant", "related", "documented",
    "precondition", "preconditions", "applies", "apply", "observed",
    "outcome", "result", "evaluated", "conforms", "requirement",
}

# Terms that commonly introduce a new externally-observable rule.  If one is
# absent from every linked source fact, it must not silently appear in a story
# or acceptance criterion.
_ASSERTIVE_FACT_TERMS = {
    "automatic", "automatically", "authorize", "authorized", "block",
    "delete", "deleted", "deny", "denied", "encrypt", "encrypted", "error",
    "escalate", "escalated", "escalation", "expire", "expired",
    "invalid", "lock", "locked", "permission", "reject", "rejected", "retain",
    "retry", "scan", "scanned", "timeout", "virus",
}

# Lower-risk behavioral terms are review signals rather than proof of an
# invented requirement.  They are checked only when used as actions so nouns
# such as "audit logs" cannot create a false unsupported-behavior defect.
_REVIEW_FACT_TERMS = {"fail", "failure", "notify", "record", "warning"}

_FACT_ALIASES = {
    "alert": "notify",
    "notification": "notify",
    "notify": "notify",
    "captur": "record",
    "capture": "record",
    "log": "record",
    "record": "record",
}

_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|mustn't|without)\b",
    re.IGNORECASE,
)

_EXPLICIT_PRIORITY_PATTERNS = {
    "Critical": (
        r"\bpriority\s*[:=-]?\s*critical\b",
        r"\bcritical\s+priority\b",
        r"\bbusiness[- ]critical\b",
    ),
    "High": (
        r"\bpriority\s*[:=-]?\s*high\b",
        r"\bhigh\s+priority\s+requirement\b",
        r"\burgent(?:ly)?\b",
        r"\bmust be delivered immediately\b",
    ),
    "Low": (
        r"\bpriority\s*[:=-]?\s*low\b",
        r"\blow\s+priority\s+requirement\b",
        r"\bnice[- ]to[- ]have\b",
        r"\boptional enhancement\b",
    ),
}


def meaningful_tokens(text: str) -> set[str]:
    """Return domain-bearing tokens, excluding generic requirement language."""
    return {token for token in tokenize(text or "") if token not in _GENERIC_TERMS}


def _fact_stem(token: str) -> str:
    """Apply deliberately small morphology normalization for fact comparison."""
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ied"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _canonical_fact_token(token: str) -> str:
    stem = _fact_stem(token)
    return _FACT_ALIASES.get(stem, stem)


def fact_tokens(text: str) -> set[str]:
    """Return normalized proposition tokens without Given/When/Then scaffolding."""
    return {
        _canonical_fact_token(token)
        for token in _FACT_TOKEN_RE.findall((text or "").lower())
        if token not in _FACT_SCAFFOLDING and len(token) > 1
    }


def source_fact_texts(requirements: Sequence) -> list[str]:
    """Build the internal fact ledger for linked requirements.

    The ledger intentionally contains only canonical requirement text and its
    verified evidence quotes.  It is internal and does not change the response
    contract.
    """
    facts: list[str] = []
    for req in requirements:
        text = (getattr(req, "text", "") or "").strip()
        if text:
            facts.append(text)
        for evidence in getattr(req, "evidence", []) or []:
            quote = (getattr(evidence, "quote", "") or "").strip()
            if quote:
                facts.append(quote)
    return list(dict.fromkeys(facts))


def unsupported_fact_terms(text: str, sources: Iterable[str]) -> set[str]:
    """Return high-risk behavioral assertions absent from linked source facts."""
    source_tokens: set[str] = set()
    for source in sources:
        source_tokens.update(fact_tokens(source))
    candidate_tokens = fact_tokens(text)
    unsupported = candidate_tokens - source_tokens
    assertive_stems = {_canonical_fact_token(term) for term in _ASSERTIVE_FACT_TERMS}
    return unsupported & assertive_stems


def unsupported_review_terms(text: str, sources: Iterable[str]) -> set[str]:
    """Return uncertain behavioral additions that should trigger review.

    Unlike high-risk fact checks, these terms are required to occur in an
    action position.  This intentionally distinguishes "filter audit logs"
    (``logs`` is an object) from "the system shall log access" (``log`` is an
    asserted action).
    """
    source_tokens: set[str] = set()
    for source in sources:
        source_tokens.update(fact_tokens(source))
    unsupported = fact_tokens(text) - source_tokens
    candidates = unsupported & _REVIEW_FACT_TERMS
    if not candidates:
        return set()

    lowered = (text or "").lower()
    asserted: set[str] = set()
    if "notify" in candidates and re.search(
        r"\b(?:shall|must|will|should|can|may|to|want(?:s)?(?:\s+to)?)\s+"
        r"(?:\w+\s+){0,2}(?:notify|alert)\b|\bsend(?:s|ing)?\b[^.;]{0,40}\bnotifications?\b",
        lowered,
    ):
        asserted.add("notify")
    if "record" in candidates and re.search(
        r"\b(?:shall|must|will|should|can|may|to|want(?:s)?(?:\s+to)?)\s+"
        r"(?:\w+\s+){0,1}(?:record|capture|log)\b",
        lowered,
    ):
        asserted.add("record")
    for term in candidates - {"notify", "record"}:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            asserted.add(term)
    return asserted


def _claim_clauses(text: str) -> list[str]:
    """Return candidate claim clauses without Given/When scaffolding."""
    claims: list[str] = []
    for part in re.split(r"\s*;\s*|(?<=[.!?])\s+", text or ""):
        cleaned = part.strip()
        if not cleaned:
            continue
        then_parts = re.split(r"\bthen\b", cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(then_parts) == 2 and then_parts[1].strip():
            cleaned = then_parts[1].strip()
        claims.extend(split_requirement_clauses(cleaned))
    return claims


def _clause_relation_score(source: str, candidate: str) -> float:
    source_tokens = fact_tokens(source) - {"not", "never", "no", "without"}
    candidate_tokens = fact_tokens(candidate) - {"not", "never", "no", "without"}
    if not source_tokens or not candidate_tokens:
        return lexical_support(source, candidate)
    containment = len(source_tokens & candidate_tokens) / min(
        len(source_tokens), len(candidate_tokens)
    )
    return max(lexical_support(source, candidate), containment)


def evaluate_polarity(text: str, sources: Iterable[str]) -> str:
    """Evaluate candidate text polarity against sources.

    Returns one of:
    - "ENTAILED": Polarity matches the closest related source clause.
    - "CONTRADICTED": Polarity contradicts the closest related source clause.
    - "NOT_COVERED": Omission or no related source clause found.
    """
    source_clauses = []
    for source in sources:
        if source:
            source_clauses.extend(split_requirement_clauses(source))
    candidate_clauses = _claim_clauses(text)

    if not source_clauses or not candidate_clauses:
        return "NOT_COVERED"

    best_source = None
    best_candidate = None
    best_score = -1.0
    for source_clause in source_clauses:
        for candidate_clause in candidate_clauses:
            score = _clause_relation_score(source_clause, candidate_clause)
            if score > best_score:
                best_score = score
                best_source = source_clause
                best_candidate = candidate_clause

    if best_score < 0.25 or best_source is None or best_candidate is None:
        return "NOT_COVERED"

    related_source_tokens = fact_tokens(best_source) - {
        "not", "never", "no", "without",
    }
    related_candidate_tokens = fact_tokens(best_candidate) - {
        "not", "never", "no", "without",
    }
    if len(related_source_tokens & related_candidate_tokens) < 2:
        return "NOT_COVERED"

    candidate_negative = bool(_NEGATION_RE.search(best_candidate))
    source_negative = bool(_NEGATION_RE.search(best_source))
    if candidate_negative != source_negative:
        source_tokens = fact_tokens(best_source) - {"not", "never", "no", "without"}
        candidate_tokens = fact_tokens(best_candidate) - {"not", "never", "no", "without"}
        shared = source_tokens & candidate_tokens
        source_coverage = len(shared) / len(source_tokens) if source_tokens else 0.0
        # A polarity mismatch is a contradiction only when the candidate
        # explicitly asserts the same proposition.  Merely omitting a negative
        # source clause is NOT_COVERED.
        if len(shared) < 2 or source_coverage < 0.60:
            return "NOT_COVERED"
        return "CONTRADICTED"
    return "ENTAILED"


def has_polarity_conflict(text: str, sources: Iterable[str]) -> bool:
    """Detect an introduced or removed negation on an otherwise related fact."""
    return evaluate_polarity(text, sources) == "CONTRADICTED"


def split_requirement_clauses(text: str) -> list[str]:
    """Split a canonical requirement into clauses that should be covered."""
    source = text or ""
    raw = re.split(
        r"\s*;\s*|\s+(?=without\b)|\s+and\s+(?=(?:shall|must|will|should|may|allows?|retains?|records?|displays?|sends?|includes?|enforces?|provides?|produces?|identifies?|attaches?|scans?)\b)",
        source,
        flags=re.I,
    )
    clauses: list[str] = []
    for part in raw:
        part = part.strip(" .")
        # Expand Oxford-comma enumerations so criteria cannot cover only the
        # first one or two mandatory events/filters and still score perfectly.
        list_match = re.match(r"^(.*?\b(?:for|by))\s+([^.;]+?,[^.;]+?,\s*(?:and|or)\s+[^.;]+)$", part, re.I)
        if list_match:
            prefix, values = list_match.groups()
            items = [item.strip() for item in re.split(r",\s*|,?\s+(?:and|or)\s+", values) if item.strip()]
            clauses.extend(
                f"{prefix} {item}" for item in items if len(meaningful_tokens(item)) >= 1
            )
        elif len(meaningful_tokens(part)) >= 2:
            clauses.append(part)
    return clauses


def clause_coverage(requirements: Sequence, criteria: Sequence[str]) -> float:
    """Measure how many source clauses are represented by acceptance criteria.

    A clause is represented only when a criterion entails that clause.  An
    omitted clause and a contradiction are both uncovered, but only the latter
    is reported as a polarity defect by callers.
    """
    clauses = [
        clause
        for req in requirements
        for clause in split_requirement_clauses(getattr(req, "text", "") or "")
    ]
    if not clauses:
        return 1.0

    covered = sum(
        1
        for clause in clauses
        if any(evaluate_polarity(criterion, [clause]) == "ENTAILED" for criterion in criteria)
    )

    return covered / len(clauses)


def infer_requirement_priority(text: str, proposed: str | None = None) -> str:
    """Infer priority only from explicit source priority or urgency language.

    Normative words such as ``shall`` and ``must`` describe obligation, not
    backlog importance, and therefore intentionally do not upgrade priority.
    """
    source = text or ""
    for priority in ("Critical", "High", "Low"):
        if any(re.search(pattern, source, re.I) for pattern in _EXPLICIT_PRIORITY_PATTERNS[priority]):
            return priority
    return "Medium"


def normalize_story_points(value, source_texts: Sequence[str] = ()) -> int:
    """Return a valid Fibonacci estimate, deriving one when model output is invalid."""
    allowed = {1, 2, 3, 5, 8}
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = 0
    if numeric in allowed:
        return numeric

    combined = " ".join(source_texts)
    token_count = len(fact_tokens(combined))
    clause_count = sum(len(split_requirement_clauses(text)) for text in source_texts)
    if token_count <= 6 and clause_count <= 1:
        return 1
    if token_count <= 12 and clause_count <= 1:
        return 2
    if token_count <= 24 and clause_count <= 2:
        return 3
    if token_count <= 45 and clause_count <= 4:
        return 5
    return 8


def infer_requirement_category(text: str, labels: Sequence[str] = ()) -> str:
    """Map source language to a stable, user-facing category."""
    lowered = (text or "").lower()
    categories = (
        ("Security & Access Control", ("authentication", "multi-factor", "mfa", "permission", "role", "access", "encrypt")),
        ("Audit & Compliance", ("audit", "immutable", "compliance", "regulation")),
        ("Case Management", ("case", "queue", "status transition", "analyst", "supervisor")),
        ("Notifications & Escalation", ("notify", "notification", "escalat", "on-call", "warning")),
        ("Reporting & Export", ("report", "export", "csv", "pdf", "dashboard")),
        ("Data Retention", ("retain", "retention", "archive", "delete", "storage")),
        ("Performance & Reliability", ("latency", "response time", "availability", "throughput", "sla", "performance")),
        ("Integration", ("api", "webhook", "integration", "third-party")),
    )
    for category, terms in categories:
        if any(term in lowered for term in terms):
            return category
    if "BR" in labels:
        return "Business Rules"
    if "NFR" in labels:
        return "Quality Attributes"
    return "Functional Capability"


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


def check_different_languages(text1: str, text2: str, lang1: str | None = None, lang2: str | None = None) -> bool:
    """Return True if languages are different (metadata check or script check)."""
    if lang1 and lang2:
        l1 = str(lang1).split("-")[0].lower()
        l2 = str(lang2).split("-")[0].lower()
        if l1 != l2:
            return True

    # Arabic vs English check
    has_ar1 = bool(re.search(r"[\u0600-\u06FF]", text1 or ""))
    has_ar2 = bool(re.search(r"[\u0600-\u06FF]", text2 or ""))
    if has_ar1 != has_ar2:
        return True

    return False
