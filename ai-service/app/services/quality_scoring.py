"""Honest, source-aware quality scoring for the unchanged V1 contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Sequence

from app.services.semantic_quality import (
    clause_coverage,
    has_polarity_conflict,
    is_substantive,
    lexical_support,
    meaningful_tokens,
    source_fact_texts,
    story_alignment,
    unsupported_fact_terms,
    unsupported_numeric_claims,
)
from app.validators.story_validator import find_duplicate_story_ids, is_generic_ac


@dataclass
class QualityScores:
    overall_score: float
    traceability_coverage: float
    groundedness_score: float
    story_completeness: float
    acceptance_criteria_quality: float
    duplicate_risk: float
    requirement_count: int
    story_count: int
    high_severity_issue_count: int

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


def _req_groundedness(req) -> float:
    evidence = getattr(req, "evidence", None) or []
    evidence_scores = [
        max(0.0, min(1.0, float(getattr(ev, "support_score", 0.0) or 0.0)))
        for ev in evidence
    ]
    strongest = max(evidence_scores, default=0.0)

    qss = getattr(req, "quote_support_score", None)
    if qss is not None:
        try:
            strongest = max(strongest, max(0.0, min(1.0, float(qss))))
        except (TypeError, ValueError):
            pass

    # Backward compatibility for callers that predate internal support scores.
    if strongest == 0.0 and evidence and qss is None:
        strongest = 0.5
    if getattr(req, "needs_review", False):
        strongest *= 0.8
    return strongest


def _story_is_complete(story) -> bool:
    return bool((getattr(story, "title", "") or "").strip()) and len(
        getattr(story, "acceptance_criteria", []) or []
    ) >= 2 and bool(getattr(story, "source_requirement_ids", []) or [])


def _severity(issue) -> str:
    return issue.get("severity", "") if isinstance(issue, dict) else getattr(issue, "severity", "")


def _story_traceable(story, requirements_by_id: dict) -> bool:
    linked = [
        requirements_by_id[rid]
        for rid in (getattr(story, "source_requirement_ids", []) or [])
        if rid in requirements_by_id
    ]
    if not linked:
        return False
    req_texts = [(getattr(req, "text", "") or "") for req in linked]
    if not any(is_substantive(text) for text in req_texts):
        return True
    story_text = f"{getattr(story, 'title', '')} {getattr(story, 'description', '')}"
    return story_alignment(req_texts, story_text) >= 0.25


def _criterion_supported(criterion, linked_requirements: Sequence) -> bool:
    text = getattr(criterion, "text", "") or ""
    if is_generic_ac(text):
        return False
    sources = source_fact_texts(linked_requirements)
    substantive = [source for source in sources if is_substantive(source)]
    if not substantive:
        return True
    if unsupported_numeric_claims(text, sources):
        return False
    if unsupported_fact_terms(text, sources) or has_polarity_conflict(text, sources):
        return False
    return max((lexical_support(source, text) for source in substantive), default=0.0) >= 0.15


def _duplicate_requirement_count(requirements: Sequence) -> int:
    prior: list[set[str]] = []
    duplicates = 0
    for req in requirements:
        tokens = meaningful_tokens(getattr(req, "text", "") or "")
        if not tokens:
            prior.append(tokens)
            continue
        duplicate = False
        for existing in prior:
            union = tokens | existing
            similarity = len(tokens & existing) / len(union) if union else 0.0
            containment = (
                min(len(tokens), len(existing)) >= 4
                and len(tokens & existing) / min(len(tokens), len(existing)) >= 0.82
            )
            if similarity >= 0.90 or containment:
                duplicate = True
                break
        duplicates += int(duplicate)
        prior.append(tokens)
    return duplicates


def compute_quality_scores(requirements: Sequence, stories: Sequence, quality_issues: Sequence) -> QualityScores:
    req_count, story_count = len(requirements), len(stories)
    requirements_by_id = {getattr(req, "id", None): req for req in requirements}

    groundedness = (
        sum(_req_groundedness(req) for req in requirements) / req_count if req_count else 1.0
    )
    if stories:
        traceable_stories = [story for story in stories if _story_traceable(story, requirements_by_id)]
        mapping_precision = len(traceable_stories) / story_count
        aligned_requirement_ids = {
            req_id
            for story in traceable_stories
            for req_id in (getattr(story, "source_requirement_ids", []) or [])
            if req_id in requirements_by_id
        }
        actionable_ids = {
            req_id for req_id, req in requirements_by_id.items()
            if not set(getattr(req, "labels", []) or []).intersection(
                {"Open Question", "Out-of-Scope", "Assumption"}
            )
        }
        requirement_coverage = (
            len(aligned_requirement_ids & actionable_ids) / len(actionable_ids)
            if actionable_ids else 1.0
        )
        traceability = (mapping_precision + requirement_coverage) / 2
    else:
        traceability = 1.0 if not requirements else 0.0
    completeness = (
        sum(1 for story in stories if _story_is_complete(story)) / story_count
        if story_count else 1.0
    )

    criteria = [ac for story in stories for ac in (getattr(story, "acceptance_criteria", []) or [])]
    if not story_count:
        ac_quality = 1.0
    elif not criteria:
        ac_quality = 0.0
    else:
        supported = 0
        for story in stories:
            linked = [
                requirements_by_id[rid]
                for rid in (getattr(story, "source_requirement_ids", []) or [])
                if rid in requirements_by_id
            ]
            supported += sum(
                1 for criterion in (getattr(story, "acceptance_criteria", []) or [])
                if _criterion_supported(criterion, linked)
            )
        criterion_precision = supported / len(criteria)
        coverage_scores = []
        for story in stories:
            linked = [
                requirements_by_id[rid]
                for rid in (getattr(story, "source_requirement_ids", []) or [])
                if rid in requirements_by_id
            ]
            coverage_scores.append(clause_coverage(
                linked,
                [getattr(ac, "text", "") or "" for ac in (getattr(story, "acceptance_criteria", []) or [])],
            ))
        fact_coverage = sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0.0
        ac_quality = min(criterion_precision, fact_coverage)

    story_duplicate_risk = len(find_duplicate_story_ids(stories)) / story_count if story_count else 0.0
    requirement_duplicate_risk = _duplicate_requirement_count(requirements) / req_count if req_count else 0.0
    duplicate_risk = max(story_duplicate_risk, requirement_duplicate_risk)
    high_count = sum(1 for issue in quality_issues if _severity(issue) == "high")
    medium_count = sum(1 for issue in quality_issues if _severity(issue) == "medium")
    low_count = sum(1 for issue in quality_issues if _severity(issue) == "low")

    overall = (
        groundedness * 0.30
        + traceability * 0.25
        + completeness * 0.15
        + ac_quality * 0.20
        + (1.0 - duplicate_risk) * 0.10
    )
    penalty = min(0.70, high_count * 0.15 + medium_count * 0.05 + low_count * 0.01)
    overall = max(0.0, overall - penalty)
    if high_count:
        overall = min(overall, 0.59)
    elif medium_count:
        overall = min(overall, 0.79)

    return QualityScores(
        overall_score=round(overall, 4),
        traceability_coverage=round(traceability, 4),
        groundedness_score=round(groundedness, 4),
        story_completeness=round(completeness, 4),
        acceptance_criteria_quality=round(ac_quality, 4),
        duplicate_risk=round(duplicate_risk, 4),
        requirement_count=req_count,
        story_count=story_count,
        high_severity_issue_count=high_count,
    )
