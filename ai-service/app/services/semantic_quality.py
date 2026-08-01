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


MIN_STORY_ALIGNMENT = 0.40


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
    "automatic", "automatically", "authorize",
    "authorized", "block", "delete", "deleted", "deny", "denied",
    "delay", "display", "encrypt", "encrypted", "error",
    "escalate", "escalated", "escalation", "expire", "expired",
    "include", "invalid", "lock", "locked", "permission", "proceed",
    "reject", "rejected", "retain", "retrieve", "retry", "scan", "scanned",
    "timeout", "update", "virus",
}

# Lower-risk behavioral terms are review signals rather than proof of an
# invented requirement.  They are checked only when used as actions so nouns
# such as "audit logs" cannot create a false unsupported-behavior defect.
_REVIEW_FACT_TERMS = {"fail", "failure", "notify", "record", "test", "warning"}

_FACT_ALIASES = {
    "accessibl": "access",
    "accessible": "access",
    "admin": "administrator",
    "administrative": "administrator",
    "authenticat": "authentication",
    "authenticate": "authentication",
    "authentication": "authentication",
    "alert": "notify",
    "notification": "notify",
    "notify": "notify",
    "captur": "record",
    "capture": "record",
    "delet": "delete",
    "deleted": "delete",
    "invitation": "invite",
    "inviting": "invite",
    "invited": "invite",
    "mfa": "authentication",
    "includ": "include",
    "inform": "notify",
    "preserv": "retain",
    "preserve": "retain",
    "recover": "reset",
    "retention": "retain",
    "retrieval": "retrieve",
    "retriev": "retrieve",
    "log": "record",
    "record": "record",
    "request": "request",
    "submit": "request",
    "submitt": "request",
    "updat": "update",
}

_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|mustn't|without|instead\s+of)\b",
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
    tokens = {
        _canonical_fact_token(token)
        for token in _FACT_TOKEN_RE.findall((text or "").lower())
        if token not in _FACT_SCAFFOLDING and len(token) > 1
    }
    lowered = (text or "").lower()
    if re.search(r"\bmulti[-\s]+factor\s+authentication\b", lowered):
        tokens.add("authentication")
    return tokens


def proposition_support(source: str, candidate: str) -> float:
    """Return deterministic proposition support after fact normalization.

    This complements ``lexical_support`` for safe paraphrases such as
    ``MFA``/``multi-factor authentication`` and ``invite``/``invitation``.
    Recall remains authoritative because a short source clause may be expressed
    inside a longer story or acceptance criterion.
    """
    source_tokens = fact_tokens(source)
    candidate_tokens = fact_tokens(candidate)
    if not source_tokens or not candidate_tokens:
        return lexical_support(source, candidate)

    source_numbers = normalized_numbers(source)
    candidate_numbers = normalized_numbers(candidate)
    # Numeric safety is enforced separately by ``unsupported_numeric_claims``.
    # At the similarity layer, reject only when the two propositions contain
    # entirely different numbers. This permits a complete source clause to
    # contain additional constraints omitted by an extracted requirement; that
    # omission is a completeness problem, not a contradiction.
    if source_numbers and candidate_numbers and source_numbers.isdisjoint(candidate_numbers):
        return 0.0

    shared = source_tokens & candidate_tokens
    recall = len(shared) / len(source_tokens)
    precision = len(shared) / len(candidate_tokens)
    normalized_score = (0.80 * recall) + (0.20 * precision)
    return round(max(lexical_support(source, candidate), normalized_score), 4)


def evidence_clause_candidates(text: str) -> list[str]:
    """Return exact, minimal sentence/window candidates from source text."""
    source = text or ""
    candidates = [source.strip()]

    # Match through punctuation inside a proposition and stop only when the
    # punctuation is followed by whitespace/end-of-text.  A negated character
    # class (``[^.!?]+``) incorrectly split common requirement values such as
    # 2.0 seconds, TLS 1.3, semantic versions, IP addresses, and 99.9% uptime.
    sentence_spans = [
        (match.start(), match.end(), match.group(0).strip())
        for match in re.finditer(
            r".+?(?:[.!?]+(?=\s|$)|$)",
            source,
            flags=re.DOTALL,
        )
        if match.group(0).strip()
    ]
    candidates.extend(sentence for _, _, sentence in sentence_spans)

    # PDFs and DOCX conversions commonly preserve list items as wrapped lines.
    # Treat each bullet as a bounded proposition so a section heading or an
    # adjacent requirement cannot dilute an otherwise exact citation.
    bullet_pattern = re.compile(
        r"(?:^|\n)\s*[-*\u2022]\s*(.+?)(?=(?:\n\s*[-*\u2022]\s)|(?:\n\s*#{1,6}\s)|\Z)",
        flags=re.DOTALL,
    )
    candidates.extend(
        match.group(1).strip()
        for match in bullet_pattern.finditer(source)
        if match.group(1).strip()
    )

    # Composite requirements may span adjacent source sentences. Preserve the
    # exact substring, including its original whitespace, for public quoting.
    for index in range(len(sentence_spans)):
        for window_size in (2, 3):
            end_index = index + window_size - 1
            if end_index >= len(sentence_spans):
                continue
            start = sentence_spans[index][0]
            end = sentence_spans[end_index][1]
            window = source[start:end].strip()
            if window and len(window) <= 1200:
                candidates.append(window)

    # Atomic requirements may need only one side of a semicolon, while the
    # complete sentence above remains available for composite requirements.
    for _, _, sentence in sentence_spans:
        candidates.extend(
            part.strip()
            for part in re.split(r"\s*;\s*", sentence)
            if part.strip()
        )

    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def best_evidence_clause(requirement: str, source_text: str) -> tuple[float, str]:
    """Select the exact source clause that best supports a requirement."""
    candidates = evidence_clause_candidates(source_text)
    if not candidates:
        return 0.0, ""
    scored = [
        (proposition_support(requirement, candidate), candidate)
        for candidate in candidates
    ]
    # Prefer the shortest exact source span when support is equal. This prevents
    # full chunks containing unrelated material from becoming public quotes.
    return max(scored, key=lambda item: (item[0], -len(item[1])))


def _normalized_proposition_text(text: str) -> str:
    return re.sub(r"\W+", " ", text or "", flags=re.UNICODE).strip().lower()


def _exact_or_contained_entailment(text: str, sources: Iterable[str]) -> bool:
    """Recognize exact source facts before clause-level polarity comparison."""
    candidate = _normalized_proposition_text(text)
    if not candidate:
        return False
    candidate_negative = bool(_NEGATION_RE.search(text or ""))

    for source in sources:
        normalized_source = _normalized_proposition_text(source)
        if not normalized_source:
            continue
        source_negative = bool(_NEGATION_RE.search(source or ""))
        if candidate_negative != source_negative:
            continue
        if candidate == normalized_source:
            return True
        if (
            (candidate in normalized_source or normalized_source in candidate)
            and proposition_support(text, source) >= 0.90
        ):
            return True
    return False


def access_control_entails(text: str, sources: Iterable[str]) -> bool:
    """Recognize the negative consequence of a source-exclusive permission.

    ``Only ROLE may ACTION`` entails that a non-ROLE actor is denied that same
    action. The rule is role/action based and is not tied to administrators.
    """
    candidate = re.sub(r"\s+", " ", text or "").strip().lower()
    denied = bool(
        re.search(
            r"\b(?:den(?:y|ied)|reject(?:ed)?|block(?:ed)?|prevent(?:ed)?|"
            r"cannot|can't|not\s+allowed|not\s+permitted)\b",
            candidate,
        )
    )
    if not denied:
        return False

    patterns = (
        re.compile(
            r"\bonly\s+(?P<role>[a-z][a-z0-9 _-]{1,60}?)\s+"
            r"(?:may|can|shall|must|are\s+allowed\s+to|are\s+permitted\s+to)\s+"
            r"(?P<action>[^.;]+)",
            re.I,
        ),
        re.compile(
            r"\b(?:allow|allows|permit|permits)\s+only\s+"
            r"(?P<role>[a-z][a-z0-9 _-]{1,60}?)\s+to\s+(?P<action>[^.;]+)",
            re.I,
        ),
    )

    candidate_tokens = fact_tokens(candidate)
    for source in sources:
        normalized_source = re.sub(r"\s+", " ", source or "").strip()
        for pattern in patterns:
            match = pattern.search(normalized_source)
            if not match:
                continue
            role_tokens = fact_tokens(match.group("role"))
            action_tokens = fact_tokens(match.group("action"))
            if not role_tokens or not action_tokens:
                continue
            role_pattern = "|".join(
                re.escape(role) + "s?" for role in sorted(role_tokens)
            )
            excluded_role = bool(
                re.search(rf"\bnon[-\s]?(?:{role_pattern})\b", candidate)
                or re.search(
                    rf"\bnot\s+(?:an?\s+|the\s+)?(?:{role_pattern})\b",
                    candidate,
                )
                or re.search(r"\b(?:other|unauthorized)\s+(?:user|users|actor|actors|role|roles)\b", candidate)
            )
            action_overlap = len(action_tokens & candidate_tokens) / len(action_tokens)
            if excluded_role and action_overlap >= 0.40:
                return True
    return False


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
    sources = list(sources)
    source_tokens: set[str] = set()
    for source in sources:
        source_tokens.update(fact_tokens(source))
    candidate_tokens = fact_tokens(text)
    unsupported = candidate_tokens - source_tokens
    assertive_stems = {_canonical_fact_token(term) for term in _ASSERTIVE_FACT_TERMS}
    result = unsupported & assertive_stems
    if "deny" in result and access_control_entails(text, sources):
        result.remove("deny")
    if "retain" in result and retention_entails(text, sources):
        result.remove("retain")
    return result


def retention_entails(text: str, sources: Iterable[str]) -> bool:
    """Recognize preservation implied by non-destructive record handling.

    Soft deletion, archival, and an explicit prohibition on permanent deletion
    entail retaining the record. The rule is generic and applies to records,
    files, assets, cases, and other persisted entities.
    """
    candidate_tokens = fact_tokens(text)
    if "retain" not in candidate_tokens:
        return False
    for source in sources:
        lowered = re.sub(r"\s+", " ", source or "").lower()
        if (
            re.search(r"\bsoft[- ]?delet(?:e|ed|ion|ing)?\b", lowered)
            or re.search(
                r"\b(?:cannot|can not|must not|shall not|never|not)\b"
                r"[^.;]{0,60}\bpermanent(?:ly)?\s+delet",
                lowered,
            )
            or re.search(r"\b(?:archive|archived|mark(?:ed)?\s+as\s+retired)\b", lowered)
        ):
            return True
    return False


def complete_requirement_from_evidence(requirement: str, evidence: str) -> str:
    """Restore source-side numeric constraints omitted from a same-language requirement.

    The function returns the original text unless the evidence is a strongly
    related bounded clause containing additional numeric facts. In that case,
    the complete source clause is safer than publishing a silently weakened
    normalized requirement.
    """
    requirement = (requirement or "").strip()
    evidence = (evidence or "").strip()
    if not requirement or not evidence:
        return requirement
    missing_numbers = normalized_numbers(evidence) - normalized_numbers(requirement)
    if not missing_numbers:
        return requirement
    if check_different_languages(requirement, evidence):
        return requirement
    if proposition_support(requirement, evidence) < 0.60:
        return requirement
    if has_polarity_conflict(requirement, [evidence]):
        return requirement

    cleaned = re.sub(r"^\s*[-*\u2022]\s*", "", evidence).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned if len(cleaned) <= 800 else requirement


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
        r"(?:\w+\s+){0,2}(?:notify|alert|inform)\b|"
        r"\bsend(?:s|ing)?\b[^.;]{0,40}\bnotifications?\b|"
        r"\b(?:is|are|was|were|be)\s+informed\b",
        lowered,
    ):
        asserted.add("notify")
    if "record" in candidates and re.search(
        r"\b(?:shall|must|will|should|can|may|to|want(?:s)?(?:\s+to)?)\s+"
        r"(?:\w+\s+){0,1}(?:record|capture|log)\b",
        lowered,
    ):
        asserted.add("record")
    if "record" in candidates and re.search(
        r"\b(?:is|are|was|were|be)\s+(?:recorded|logged|captured)\b",
        lowered,
    ):
        asserted.add("record")
    if "test" in candidates and re.search(r"\btest(?:s|ed|ing)?\b", lowered):
        asserted.add("test")
    for term in candidates - {"notify", "record", "test"}:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            asserted.add(term)
    return asserted


def _claim_clauses(text: str) -> list[str]:
    """Return candidate claims while retaining Given/When coverage context."""
    claims: list[str] = []
    for part in re.split(r"\s*;\s*|(?<=[.!?])\s+", text or ""):
        cleaned = part.strip()
        if not cleaned:
            continue
        claims.append(cleaned)
        then_parts = re.split(r"\bthen\b", cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(then_parts) == 2 and then_parts[1].strip():
            claims.append(then_parts[1].strip())
        claims.extend(split_requirement_clauses(cleaned))
    return list(dict.fromkeys(claims))


def _clause_relation_score(source: str, candidate: str) -> float:
    source_tokens = fact_tokens(source) - {"not", "never", "no", "without"}
    candidate_tokens = fact_tokens(candidate) - {"not", "never", "no", "without"}
    if not source_tokens or not candidate_tokens:
        return proposition_support(source, candidate)
    containment = len(source_tokens & candidate_tokens) / min(
        len(source_tokens), len(candidate_tokens)
    )
    return max(proposition_support(source, candidate), containment)


def evaluate_polarity(text: str, sources: Iterable[str]) -> str:
    """Evaluate candidate text polarity against sources.

    Returns one of:
    - "ENTAILED": Polarity matches the closest related source clause.
    - "CONTRADICTED": Polarity contradicts the closest related source clause.
    - "NOT_COVERED": Omission or no related source clause found.
    """
    sources = list(sources)
    if _exact_or_contained_entailment(text, sources):
        return "ENTAILED"
    if access_control_entails(text, sources):
        return "ENTAILED"

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

    covered = 0
    for clause in clauses:
        clause_is_covered = False
        for criterion in criteria:
            if access_control_entails(criterion, [clause]):
                clause_is_covered = True
                break
            if (
                proposition_support(clause, criterion) >= 0.25
                and evaluate_polarity(criterion, [clause]) == "ENTAILED"
            ):
                clause_is_covered = True
                break
        covered += int(clause_is_covered)

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
        ("Security & Access Control", ("authentication", "multi-factor", "mfa", "permission", "role", "access", "encrypt", "tls", "ssl", "credential")),
        ("Audit & Compliance", ("audit", "immutable", "compliance", "regulation")),
        ("Case Management", ("case", "queue", "status transition", "analyst", "supervisor")),
        ("Notifications & Escalation", ("notify", "notification", "escalat", "on-call", "warning")),
        # Performance must be checked before presentation/reporting terms. A
        # dashboard with a latency/load/uptime target is a quality attribute,
        # not automatically a reporting feature.
        ("Performance & Reliability", ("latency", "response time", "load time", "load and become interactive", "uptime", "availability", "throughput", "concurrent load", "active sessions", "sla", "performance", "reliability")),
        ("Reporting & Export", ("report", "reporting", "export", "csv", "pdf", "analytics dashboard", "reporting dashboard")),
        ("Data Retention", ("retain", "retention", "archive", "delete", "storage")),
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


def normalize_requirement_labels(text: str, labels: Sequence[str]) -> list[str]:
    """Resolve contradictory FR/NFR multi-label output conservatively.

    A requirement may legitimately also be a business rule, but a concrete
    capability should not be exported as NFR merely because its purpose uses a
    vague quality word such as "fast". Explicit measurable or recognized
    quality attributes retain NFR.
    """
    normalized = list(dict.fromkeys(str(label) for label in labels if label))
    label_set = set(normalized)
    lowered = (text or "").lower()
    nfr_indicators = (
        "latency", "response time", "load time", "uptime", "availability",
        "throughput", "concurrent", "scalab", "reliab", "encrypt", "tls",
        "ssl", "accessibility", "maintainab", "recover", "failover", "sla",
    )
    measurable_quality = bool(
        re.search(r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|seconds?|%|percent|sessions?|requests?)\b", lowered)
    )
    if (
        {"FR", "NFR"}.issubset(label_set)
        and not measurable_quality
        and not any(term in lowered for term in nfr_indicators)
    ):
        normalized = [label for label in normalized if label != "NFR"]
    business_rule_indicators = (
        "only", "unless", "except", "cannot", "must not", "shall not",
        "approval", "limit", "up to", "at most", "at least", "exceed",
        "retention", "retain", "soft-delete", "soft delete", "required if",
    )
    if "FR" in normalized and "BR" in normalized and not any(
        term in lowered for term in business_rule_indicators
    ):
        normalized = [label for label in normalized if label != "BR"]
    return normalized


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
    if ev_numbers and req_numbers and ev_numbers.isdisjoint(req_numbers):
        return 0.0

    overlap = req_tokens & ev_tokens
    recall = len(overlap) / len(req_tokens)
    precision = len(overlap) / len(ev_tokens)
    score = (0.75 * recall) + (0.25 * precision)
    return round(max(0.0, min(1.0, score)), 4)


def story_alignment(requirement_texts: Sequence[str], story_text: str) -> float:
    """Return normalized clause alignment for a story and linked requirements."""
    if not requirement_texts:
        return 0.0
    clauses = [
        clause
        for text in requirement_texts
        for clause in (split_requirement_clauses(text) or [text])
    ]
    return max(
        (proposition_support(clause, story_text) for clause in clauses),
        default=0.0,
    )


def clear_story_mapping_mismatch(
    requirement_texts: Sequence[str],
    story_text: str,
    alignment: float | None = None,
) -> bool:
    """Return True only when evidence beyond a low lexical score shows mismatch."""
    if not requirement_texts:
        return False
    score = (
        story_alignment(requirement_texts, story_text)
        if alignment is None
        else alignment
    )
    if score >= MIN_STORY_ALIGNMENT:
        return False
    return bool(
        unsupported_numeric_claims(story_text, requirement_texts)
        or has_polarity_conflict(story_text, requirement_texts)
        or unsupported_fact_terms(story_text, requirement_texts)
    )


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
