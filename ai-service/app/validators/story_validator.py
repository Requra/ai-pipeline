"""
User-story validation helpers (pure functions).

Used by the generate node to flag low-quality stories (and to drive quality
scoring later). These functions never mutate the stories — they only report
issues — so callers stay in control of repair vs. warn.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence

from app.services.semantic_quality import (
    clause_coverage,
    clear_story_mapping_mismatch,
    has_polarity_conflict,
    is_substantive,
    fact_tokens,
    MIN_STORY_ALIGNMENT,
    meaningful_tokens,
    normalized_numbers,
    source_fact_texts,
    split_requirement_clauses,
    story_alignment,
    unsupported_fact_terms,
    unsupported_numeric_claims,
)

# Phrases that signal a non-specific, boilerplate acceptance criterion.
_GENERIC_AC_PATTERNS = (
    "requirement is implemented as specified",
    "implemented as specified",
    "works as expected",
    "works correctly",
    "as expected",
    "as specified",
    "functions correctly",
    "behaves correctly",
    "documented preconditions apply",
    "capability is exercised",
    "capability has been exercised",
    "observed outcome conforms to",
    "result is evaluated",
)

# One source-specific criterion is better than manufacturing a second generic
# or duplicate criterion for an atomic one-clause requirement.
MIN_ACCEPTANCE_CRITERIA = 1


def is_generic_ac(text: str) -> bool:
    """True when an acceptance criterion is boilerplate rather than specific."""
    if not text or not text.strip():
        return True
    lowered = text.strip().lower()
    # Very short criteria are almost never specific/testable.
    if len(lowered) < 15:
        return True
    return any(pat in lowered for pat in _GENERIC_AC_PATTERNS)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def find_duplicate_acceptance_criterion_ids(story, requirements: Sequence = ()) -> List[str]:
    """Return redundant AC IDs using proposition-level similarity.

    Numeric differences and opposite polarity keep criteria distinct. This
    avoids fixture-specific wording rules while detecting two criteria that
    merely restate the same measurable outcome.
    """
    criteria = list(getattr(story, "acceptance_criteria", []) or [])
    source_clauses = [
        clause
        for requirement in requirements
        for clause in split_requirement_clauses(
            getattr(requirement, "text", "") or ""
        )
    ]

    def best_source_clause(text: str) -> tuple[int | None, float]:
        if not source_clauses:
            return None, 0.0
        scores = [
            story_alignment([clause], text)
            for clause in source_clauses
        ]
        index = max(range(len(scores)), key=scores.__getitem__)
        return index, scores[index]

    duplicates: List[str] = []
    positive_outcome = re.compile(r"\b(?:allow(?:ed)?|accept(?:ed)?|permit(?:ted)?|succeed(?:s|ed)?|complete[sd]?)\b", re.I)
    negative_outcome = re.compile(r"\b(?:den(?:y|ied)|reject(?:ed)?|prevent(?:ed|s)?|block(?:ed|s)?|fail(?:ed|s)?)\b", re.I)
    for index, criterion in enumerate(criteria):
        text = getattr(criterion, "text", "") or ""
        tokens = fact_tokens(text)
        if not tokens:
            continue
        for prior in criteria[:index]:
            prior_text = getattr(prior, "text", "") or ""
            prior_tokens = fact_tokens(prior_text)
            if not prior_tokens:
                continue
            if normalized_numbers(text) != normalized_numbers(prior_text):
                continue
            text_outcome = (
                bool(positive_outcome.search(text)),
                bool(negative_outcome.search(text)),
            )
            prior_outcome = (
                bool(positive_outcome.search(prior_text)),
                bool(negative_outcome.search(prior_text)),
            )
            if text_outcome != prior_outcome and any(text_outcome + prior_outcome):
                continue
            if has_polarity_conflict(text, [prior_text]) or has_polarity_conflict(
                prior_text, [text]
            ):
                continue
            union = tokens | prior_tokens
            similarity = len(tokens & prior_tokens) / len(union) if union else 0.0
            containment = len(tokens & prior_tokens) / min(len(tokens), len(prior_tokens))
            source_index, source_score = best_source_clause(text)
            prior_source_index, prior_source_score = best_source_clause(prior_text)
            same_atomic_fact = (
                source_index is not None
                and source_index == prior_source_index
                and source_score >= 0.25
                and prior_source_score >= 0.25
            )
            if similarity >= 0.50 or containment >= 0.70 or same_atomic_fact:
                duplicates.append(
                    getattr(criterion, "id", "") or f"criterion-{index + 1}"
                )
                break
    return duplicates


def validate_story(story, reqs_by_id: Dict[int, object]) -> List[str]:
    """Return a list of issue codes for a single story (empty == clean)."""
    issues: List[str] = []

    title = getattr(story, "title", "") or ""
    if not title.strip():
        issues.append("missing_title")

    description = getattr(story, "description", "") or ""
    if not description.strip() or "none" in description.lower().split():
        issues.append("weak_description")

    acs = getattr(story, "acceptance_criteria", []) or []
    if len(acs) < MIN_ACCEPTANCE_CRITERIA:
        issues.append("insufficient_acceptance_criteria")
    if acs and all(is_generic_ac(getattr(ac, "text", "")) for ac in acs):
        issues.append("all_generic_acceptance_criteria")
    if find_duplicate_acceptance_criterion_ids(story):
        issues.append("duplicate_acceptance_criteria")

    src_ids = getattr(story, "source_requirement_ids", []) or []
    if not src_ids:
        issues.append("missing_source_requirement_ids")
    else:
        # If the primary source requirement carries evidence, the story should
        # carry an evidence_reference for traceability.
        primary = reqs_by_id.get(src_ids[0])
        if primary is not None and getattr(primary, "evidence", None):
            if not (getattr(story, "evidence_reference", None) or []):
                issues.append("missing_evidence_reference")

        linked = [reqs_by_id[req_id] for req_id in src_ids if req_id in reqs_by_id]
        if find_duplicate_acceptance_criterion_ids(story, linked):
            if "duplicate_acceptance_criteria" not in issues:
                issues.append("duplicate_acceptance_criteria")
        req_texts = [getattr(req, "text", "") or "" for req in linked]
        story_text = " ".join([
            title,
            description,
            *[getattr(ac, "text", "") or "" for ac in acs],
        ])
        if linked and any(is_substantive(text) for text in req_texts):
            alignment = story_alignment(req_texts, story_text)
            if (
                alignment < MIN_STORY_ALIGNMENT
                and clear_story_mapping_mismatch(req_texts, story_text, alignment)
            ):
                issues.append("incorrect_story_requirement_mapping")

        facts = source_fact_texts(linked)
        criterion_texts = [getattr(ac, "text", "") or "" for ac in acs]
        if any(is_substantive(fact) for fact in facts):
            if any(
                unsupported_numeric_claims(text, facts)
                or unsupported_fact_terms(text, facts)
                or has_polarity_conflict(text, facts)
                for text in criterion_texts
            ):
                issues.append("unsupported_acceptance_fact")
            if linked and clause_coverage(linked, criterion_texts) < 1.0:
                issues.append("missing_source_clause")

    if getattr(story, "story_points", 0) not in {1, 2, 3, 5, 8}:
        issues.append("invalid_story_points")

    return issues


def find_duplicate_story_ids(stories: Sequence) -> List[str]:
    """Return exact or near-semantic duplicates.

    Titles are presentation text and often differ even when two generated
    stories express the same proposition, so description-token similarity is
    checked independently.
    """
    seen: Dict[str, str] = {}
    prior_descriptions: List[tuple[str, set[str]]] = []
    dupes: List[str] = []
    for s in stories:
        key = _norm(getattr(s, "title", "")) + "||" + _norm(getattr(s, "description", ""))
        tokens = meaningful_tokens(getattr(s, "description", ""))
        near_duplicate = False
        if tokens:
            for _prior_id, prior_tokens in prior_descriptions:
                union = tokens | prior_tokens
                similarity = len(tokens & prior_tokens) / len(union) if union else 0.0
                if similarity >= 0.90:
                    near_duplicate = True
                    break
        if key in seen or near_duplicate:
            dupes.append(getattr(s, "id", ""))
        else:
            seen[key] = getattr(s, "id", "")
            prior_descriptions.append((getattr(s, "id", ""), tokens))
    return dupes


def validate_stories(stories: Sequence, reqs_by_id: Dict[int, object]) -> Dict[str, List[str]]:
    """Validate a batch; return {story_id: [issue codes]} for stories with issues."""
    report: Dict[str, List[str]] = {}
    for s in stories:
        issues = validate_story(s, reqs_by_id)
        if issues:
            report[getattr(s, "id", "")] = issues
    return report
