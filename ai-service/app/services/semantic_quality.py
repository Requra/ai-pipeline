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
_NUMBER_VALUE_RE = re.compile(r"\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\b")

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
    "access", "automatic", "automatically", "authorize",
    "authorized", "block", "delete", "deleted", "deny", "denied",
    "delay", "display", "encrypt", "encrypted", "error",
    "escalate", "escalated", "escalation", "expire", "expired",
    "include", "invalid", "lock", "locked", "permission", "proceed", "profile",
    "reject", "rejected", "retain", "retrieve", "retry", "scan", "scanned",
    "timeout", "update", "virus",
}

# Lower-risk behavioral terms are review signals rather than proof of an
# invented requirement.  They are checked only when used as actions so nouns
# such as "audit logs" cannot create a false unsupported-behavior defect.
_REVIEW_FACT_TERMS = {"fail", "failure", "notify", "record", "test", "warning"}

_FACT_ALIASES = {
    "able": "allow",
    "acces": "access",
    "accessibl": "access",
    "accessible": "access",
    "admin": "administrator",
    "administrative": "administrator",
    "authenticat": "authentication",
    "authenticate": "authentication",
    "authentication": "authentication",
    "alert": "notify",
    "appear": "display",
    "appearing": "display",
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
    "integration": "integrate",
    "inform": "notify",
    "grant": "authorize",
    "granted": "authorize",
    "preserv": "retain",
    "preserve": "retain",
    "proce": "proceed",
    "recover": "reset",
    "retention": "retain",
    "retrieval": "retrieve",
    "retriev": "retrieve",
    "log": "record",
    "list": "display",
    "listed": "display",
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
    # Treat compact ASR and engineering notation (`2s`, `250ms`, `5min`) as
    # the same fact dimensions as their spaced, fully written equivalents.
    # Keep the original token too; these additions only improve alignment.
    compact_units = {
        "ms": "millisecond", "msec": "millisecond", "msecs": "millisecond",
        "s": "second", "sec": "second", "secs": "second",
        "m": "minute", "min": "minute", "mins": "minute",
        "h": "hour", "hr": "hour", "hrs": "hour",
    }
    for number, unit in re.findall(r"\b(\d+(?:\.\d+)?)\s*(ms|msecs?|secs?|s|mins?|m|hrs?|h)\b", lowered):
        tokens.add(number)
        tokens.add(compact_units[unit])
    if re.search(r"\bmulti[-\s]+factor\s+authentication\b", lowered):
        tokens.add("authentication")
    return tokens


def clause_requires_review(text: str) -> bool:
    """Return whether text is an unsafe standalone requirement/citation clause.

    This is intentionally conservative: it catches detached conjunctions,
    dangling prepositions, and pronoun-led clauses with no stated antecedent.
    A complete conditional such as ``When X, the system shall Y`` is allowed.
    """
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) < 8:
        return True
    lowered = compact.lower().strip(" .;:,")
    if not lowered:
        return True
    if re.match(
        r"^(?:and|or|but|so|because|during|including|especially|also|"
        r"while|whereas|ثم|و|أو|لكن|أثناء|بما\s+في\s+ذلك)\b",
        lowered,
    ):
        return True
    if re.search(
        r"\b(?:and|or|but|because|with|without|for|to|of|in|on|at|"
        r"during|when|if|unless|و|أو|من|إلى|في|على|عند|إذا)\s*$",
        lowered,
    ):
        return True
    if re.match(
        r"^(?:it|they|them|this|that|these|those|هو|هي|هم|هذا|هذه)\s+"
        r"(?:shall|must|should|will|can|may|is|are|يجب|يمكن|سيتم|هو|هي)\b",
        lowered,
    ):
        return True
    if re.match(r"^[A-Z]{2,}(?:\s+[A-Z][A-Za-z-]*){1,3}$", compact):
        return True
    return False


def discard_unattached_leading_fragment(text: str) -> tuple[str, bool]:
    """Remove an unattached noun fragment accidentally joined to a statement.

    Speech-to-text and extraction models occasionally emit ``Directory for user
    authentication. Records must ...`` as one requirement. The first sentence
    has no requirement predicate and is not evidence for the second; retaining
    it creates a fabricated composite requirement. The same fragment can also
    appear after a valid preceding requirement when a model groups adjacent
    source statements. Remove only a short, predicate-free standalone fragment
    that is followed by a substantive requirement statement. This never joins
    or invents source text.
    """
    source = re.sub(r"\s+", " ", (text or "").strip())
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?؟])\s+", source)
        if part.strip()
    ]
    if len(sentences) < 2:
        return source, False
    predicate_pattern = re.compile(
        r"\b(?:shall|must|should|will|can|may|cannot|can't|is|are|"
        r"يجب|يمكن|لا\s+يمكن|سيتم)\b",
        re.IGNORECASE,
    )
    retained: list[str] = []
    dropped = False
    for index, sentence in enumerate(sentences):
        candidate = sentence.rstrip(".!?؟")
        following = " ".join(sentences[index + 1:])
        is_unattached_fragment = (
            bool(following)
            and not predicate_pattern.search(candidate)
            and 2 <= len(fact_tokens(candidate)) <= 8
            and bool(predicate_pattern.search(following))
        )
        if is_unattached_fragment:
            dropped = True
            continue
        retained.append(sentence)
    return " ".join(retained).strip(), dropped


def material_fact_coverage(requirement: str, evidence: str) -> float:
    """Return conservative coverage of requirement facts by an evidence clause."""
    requirement_tokens = fact_tokens(requirement)
    evidence_tokens = fact_tokens(evidence)
    if not requirement_tokens:
        return 1.0
    if not evidence_tokens:
        return 0.0
    modals = {"shall", "must", "should", "may", "can", "will"}
    requirement_tokens -= modals
    evidence_tokens -= modals
    if not requirement_tokens:
        return 1.0
    return len(requirement_tokens & evidence_tokens) / len(requirement_tokens)


def evidence_covers_material_facts(requirement: str, evidence: str) -> bool:
    """Require a public citation to carry the requirement's material facts."""
    if clause_requires_review(evidence):
        return False
    if unsupported_numeric_claims(requirement, [evidence]):
        return False
    if has_polarity_conflict(requirement, [evidence]):
        return False
    coverage = material_fact_coverage(requirement, evidence)
    token_count = len(fact_tokens(requirement))
    return coverage >= (1.0 if token_count <= 6 else 0.85)


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


def _has_enforceable_numeric_upper_bound(text: str) -> bool:
    """Return whether a numeric cap defines an enforceable business boundary.

    An explicit permission or limit (for example, "allowed to check out up to
    3 assets") entails rejection above the cap. A workload envelope (for
    example, "under load of up to 500 sessions") only defines the conditions
    under which another requirement is measured and must not be converted into
    a rejection rule.
    """
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()
    cap = r"(?:up to|at most|no more than|maximum(?:\s+of)?)\s+\d+(?:[,.]\d+)?"
    explicit_permission = re.search(
        rf"\b(?:allow(?:ed|s)?|permit(?:ted|s)?)\b[^.;()]{{0,100}}\b{cap}",
        normalized,
    )
    explicit_limit = re.search(
        r"\b(?:limit(?:ed|s)?)\b[^.;()]{0,60}\bto\s+\d+(?:[,.]\d+)?",
        normalized,
    )
    direct_modal_cap = re.search(
        rf"\b(?:may|can)\b[^.;()]{{0,80}}\b{cap}",
        normalized,
    )
    return bool(explicit_permission or explicit_limit or direct_modal_cap)


def numeric_upper_bound_entails(text: str, sources: Iterable[str]) -> bool:
    """Recognize rejection at a source-defined numeric upper boundary."""
    candidate = re.sub(r"\s+", " ", text or "").strip().lower()
    if not re.search(
        r"\b(?:den(?:y|ies|ied)|reject(?:s|ed)?|block(?:s|ed)?|prevent(?:s|ed)?|"
        r"cannot|can't|does\s+not\s+proceed|not\s+allowed|not\s+permitted)\b",
        candidate,
    ):
        return False
    candidate_numbers = normalized_numbers(candidate)
    candidate_tokens = fact_tokens(candidate)
    for source in sources:
        if not _has_enforceable_numeric_upper_bound(source):
            continue
        if not re.search(
            r"\b(?:up to|at most|no more than|maximum(?:\s+of)?|limit(?:ed)?\s+to)\s+"
            r"\d+(?:[,.]\d+)?",
            source or "",
            flags=re.IGNORECASE,
        ):
            continue
        source_numbers = normalized_numbers(source)
        source_tokens = fact_tokens(source)
        if not (candidate_numbers & source_numbers):
            continue
        if len(candidate_tokens & source_tokens) >= 2:
            return True
    return False


def _exclusive_access_scope_entails(text: str, sources: Iterable[str]) -> bool:
    """Recognize a faithful restatement of an exclusive source permission."""
    candidate = re.sub(r"\s+", " ", text or "").strip().lower()
    limited = re.search(
        r"\b(?:access|retrieval|permission)\b[^.;]{0,60}"
        r"\b(?:limited|restricted)\s+to\s+(?P<role>[a-z][a-z0-9 _-]{1,40})",
        candidate,
    )
    if not limited:
        return False
    candidate_roles = fact_tokens(limited.group("role"))
    for source in sources:
        exclusive = re.search(
            r"\bonly\s+(?P<role>[a-z][a-z0-9 _-]{1,60}?)\s+"
            r"(?:may|can|shall|must|are\s+allowed\s+to|are\s+permitted\s+to)\s+",
            source or "",
            flags=re.IGNORECASE,
        )
        if exclusive and fact_tokens(exclusive.group("role")) & candidate_roles:
            return True
    return False


def source_fact_texts(requirements: Sequence) -> list[str]:
    """Build the internal fact ledger for linked requirements.

    Verified evidence is authoritative whenever it exists. Canonical text is
    used only when the requirement has no verified source quote; otherwise a
    malformed or over-broad extraction could incorrectly authorize an
    acceptance criterion that is absent from the cited source. This is internal
    and does not change the response contract.
    """
    facts: list[str] = []
    for req in requirements:
        text = (getattr(req, "text", "") or "").strip()
        evidence_facts: list[str] = []
        for evidence in getattr(req, "evidence", []) or []:
            quote = (getattr(evidence, "quote", "") or "").strip()
            if quote:
                evidence_facts.append(quote)
        if evidence_facts:
            facts.extend(evidence_facts)
        elif text:
            facts.append(text)
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
    if "display" in result and not re.search(
        r"\b(?:display|show|appear)(?:s|ed|ing)?\b|"
        r"\b(?:is|are|was|were|be)\s+listed\b|"
        r"\b(?:shall|must|will|should|can|may|to)\s+list\b",
        text or "",
        flags=re.IGNORECASE,
    ):
        # "list" is often an object ("asset list"), not a newly asserted
        # presentation action. Keep the action check syntax-aware.
        result.remove("display")
    access_is_asserted = re.search(
        r"^\s*(?:access|retrieve|read|view)\b|"
        r"\b(?:shall|must|will|should|can|may|to)\s+"
        r"(?:access|retrieve|read|view)\b|"
        r"\b(?:accesses|retrieves|reads|views)\b|"
        r"\b(?:is|are|was|were|be)\s+accessible\b",
        text or "",
        flags=re.IGNORECASE,
    )
    access_is_entailed = access_control_entails(text, sources) or _exclusive_access_scope_entails(text, sources)
    if "access" in result and (not access_is_asserted or access_is_entailed):
        result.remove("access")
    if "deny" in result and access_control_entails(text, sources):
        result.remove("deny")
    if numeric_upper_bound_entails(text, sources):
        result.difference_update({"block", "deny", "proceed", "reject"})
    if active_prohibition_entails(text, sources):
        result.difference_update({"block", "deny", "reject", "prevent"})
    if "retain" in result and retention_entails(text, sources):
        result.remove("retain")
    return result


def active_prohibition_entails(text: str, sources: Iterable[str]) -> bool:
    """Recognize rejection/blocking entailed by an explicit source prohibition."""
    candidate = re.sub(r"\s+", " ", text or "").strip().lower()
    if not re.search(
        r"\b(?:den(?:y|ied)|reject(?:ed|s)?|block(?:ed|s)?|prevent(?:ed|s)?|"
        r"cannot|can't|does\s+not\s+allow|not\s+allowed|not\s+permitted)\b",
        candidate,
    ):
        return False
    prohibition_re = re.compile(
        r"\b(?:cannot|can't|must\s+not|shall\s+not|never|under\s+no\s+circumstances|"
        r"prohibited|strictly\s+forbidden)\b",
        re.I,
    )
    for source in sources:
        if prohibition_re.search(source or ""):
            return True
    return False


def introduces_unsupported_approval_outcome(text: str, sources: Iterable[str]) -> bool:
    """Reject approval success inferred from a rule that only requires review.

    ``Requires manager approval`` does not entail ``the request is approved``.
    The latter is an additional business decision, so it must be explicitly
    present in the source before it can appear in an acceptance criterion.
    """
    candidate = re.sub(r"\s+", " ", text or "").lower()
    asserts_success = bool(re.search(
        r"\b(?:request|application|item|record|it)\s+(?:is|are|gets?|becomes?)\s+approved\b|"
        r"\bapproval\s+(?:is\s+)?granted\b",
        candidate,
    ))
    if not asserts_success:
        return False
    for source in sources:
        source_text = re.sub(r"\s+", " ", source or "").lower()
        if not re.search(r"\bapprov(?:al|e|ed)\b", source_text):
            continue
        explicit_success = bool(re.search(
            r"\b(?:request|application|item|record|it)\s+(?:is|are|gets?|becomes?)\s+approved\b|"
            r"\bapproval\s+(?:is\s+)?granted\b|\bmanager\s+approves\b",
            source_text,
        ))
        if not explicit_success:
            return True
    return False


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
    """Restore explicit source constraints omitted from a same-language requirement.

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
    evidence_negative = bool(_NEGATION_RE.search(evidence))
    requirement_negative = bool(_NEGATION_RE.search(requirement))
    missing_negative_constraint = evidence_negative and not requirement_negative
    requirement_facts = fact_tokens(requirement)
    evidence_facts = fact_tokens(evidence)
    additional_facts = evidence_facts - requirement_facts
    # A trailing purpose, condition, exception, or scope phrase is often lost
    # when ASR inserts a sentence boundary in the middle of a statement.  If
    # the already-selected exact evidence clause contains such an extension,
    # preserve it rather than publishing a silently weakened requirement.
    material_extension = bool(additional_facts) and bool(re.search(
        r"\b(?:for|to|so\s+that|in\s+order\s+to|during|under|when|if|"
        r"unless|except|excluding|including|with|without|using)\b|"
        r"(?:\u0644|\u0645\u0646|\u0639\u0646\u062f|\u0625\u0630\u0627|\u0628\u0627\u0633\u062a\u062e\u062f\u0627\u0645)",
        evidence,
        flags=re.IGNORECASE,
    ))
    if not missing_numbers and not missing_negative_constraint and not material_extension:
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
    if "notify" in candidates and re.search(
        r"\b(?:and|then)\s+(?:the\s+)?(?:system|service|application|portal|it)\s+"
        r"(?:notify|alert|inform)(?:s|ed|ing)?\b|"
        r"\band\s+(?:notify|alert|inform)(?:s|ed|ing)?\b",
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
        split_claims = split_requirement_clauses(cleaned)
        if len(split_claims) > 1:
            claims.extend(split_claims)
        else:
            claims.append(cleaned)
        then_parts = re.split(r"\bthen\b", cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(then_parts) == 2 and then_parts[1].strip():
            claims.append(then_parts[1].strip())
        claims.extend(split_claims)
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
    if numeric_upper_bound_entails(text, sources):
        return "ENTAILED"
    if active_prohibition_entails(text, sources):
        return "ENTAILED"

    source_clauses = []
    for source in sources:
        if source:
            source_clauses.extend(split_requirement_clauses(source))
    candidate_clauses = _claim_clauses(text)

    if not source_clauses or not candidate_clauses:
        return "NOT_COVERED"

    any_entailed = False
    for candidate_clause in candidate_clauses:
        best_source = max(
            source_clauses,
            key=lambda source_clause: _clause_relation_score(source_clause, candidate_clause),
        )
        best_score = _clause_relation_score(best_source, candidate_clause)
        if best_score < 0.25:
            continue

        source_tokens = fact_tokens(best_source) - {"not", "never", "no", "without"}
        candidate_tokens = fact_tokens(candidate_clause) - {"not", "never", "no", "without"}
        shared = source_tokens & candidate_tokens
        if len(shared) < 2:
            continue

        candidate_negative = bool(_NEGATION_RE.search(candidate_clause))
        source_negative = bool(_NEGATION_RE.search(best_source))
        if candidate_negative != source_negative:
            source_coverage = len(shared) / len(source_tokens) if source_tokens else 0.0
            # A polarity mismatch is a contradiction only when this atomic
            # candidate clause asserts the same proposition. A negative clause
            # must never contaminate an adjacent positive clause (or vice versa).
            if source_coverage >= 0.60:
                return "CONTRADICTED"
            continue
        any_entailed = True
    return "ENTAILED" if any_entailed else "NOT_COVERED"


def has_polarity_conflict(text: str, sources: Iterable[str]) -> bool:
    """Detect an introduced or removed negation on an otherwise related fact."""
    candidate = re.sub(r"\s+", " ", text or "").lower()
    if re.search(
        r"\b(?:unlimited|unrestricted)\b|"
        r"\bwithout\s+(?:any\s+)?(?:restriction|restrictions|limit|limits)\b",
        candidate,
    ):
        for source in sources:
            if re.search(
                r"\b(?:up to|at most|no more than|maximum|max(?:imum)?|limit(?:ed)? to)\b",
                source or "",
                flags=re.IGNORECASE,
            ):
                return True
    return evaluate_polarity(text, sources) == "CONTRADICTED"


def split_requirement_clauses(text: str) -> list[str]:
    """Split a canonical requirement into clauses that should be covered."""
    source = text or ""
    # A source window can contain adjacent positive and negative propositions:
    # "records cannot be permanently deleted. They must be soft-deleted." A
    # polarity decision must compare each candidate against its related
    # sentence, not let the first negation contaminate the next requirement.
    sentences = re.split(r"(?<=[.!?؟])\s+(?=[^\d\s])", source)
    raw = []
    for sentence in sentences:
        raw.extend(re.split(
            r"\s*;\s*|\s+(?=without\b)|,?\s+and\s+(?=(?:not|never|cannot|can\s+not|shall\s+not|must\s+not)\b)|\s+and\s+(?=(?:shall|must|will|should|may|allows?|retains?|records?|displays?|sends?|includes?|enforces?|provides?|produces?|identifies?|attaches?|scans?)\b)",
            sentence,
            flags=re.I,
        ))
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
            if missing_required_numeric_claims(clause, criterion):
                continue
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
    if "Out-of-Scope" in labels:
        return "Out-of-Scope"
    if "Open Question" in labels:
        return "Open Question"
    if "Assumption" in labels:
        return "Assumption"
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


def _normalized_numeric_values(text: str) -> set[str]:
    """Return formatting-independent numeric values for completeness checks."""
    values: set[str] = set()
    for match in _NUMBER_VALUE_RE.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        try:
            numeric = float(raw)
            values.add(str(int(numeric)) if numeric.is_integer() else format(numeric, "g"))
        except ValueError:
            values.add(raw.lower())
    return values


def missing_required_numeric_claims(source: str, candidate: str) -> set[str]:
    """Return measurable source values omitted by a candidate artifact.

    This is intentionally the inverse of ``unsupported_numeric_claims``. It
    compares normalized numeric values so harmless formatting differences such
    as ``2`` versus ``2.0`` do not create a false omission.
    """
    source_values = _normalized_numeric_values(source)
    if not source_values:
        return set()
    return source_values - _normalized_numeric_values(candidate)


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
