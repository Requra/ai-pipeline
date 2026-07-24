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
    MIN_STORY_ALIGNMENT,
    meaningful_tokens,
    source_fact_texts,
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
)

# Minimum acceptance criteria per story (MVP rule).
MIN_ACCEPTANCE_CRITERIA = 2


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
