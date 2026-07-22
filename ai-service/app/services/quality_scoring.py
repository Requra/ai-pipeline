"""Honest, source-aware quality scoring for the unchanged V1 contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Sequence

from app.services.semantic_quality import (
    is_substantive,
    lexical_support,
    story_alignment,
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
    sources: List[str] = []
    for req in linked_requirements:
        req_text = getattr(req, "text", "") or ""
        sources.append(req_text)
        sources.extend(getattr(ev, "quote", "") or "" for ev in (getattr(req, "evidence", []) or []))
    substantive = [source for source in sources if is_substantive(source)]
    if not substantive:
        return True
    if unsupported_numeric_claims(text, sources):
        return False
    return max((lexical_support(source, text) for source in substantive), default=0.0) >= 0.15


def compute_quality_scores(requirements: Sequence, stories: Sequence, quality_issues: Sequence) -> QualityScores:
    req_count, story_count = len(requirements), len(stories)
    requirements_by_id = {getattr(req, "id", None): req for req in requirements}

    groundedness = (
        sum(_req_groundedness(req) for req in requirements) / req_count if req_count else 1.0
    )
    traceability = (
        sum(1 for story in stories if _story_traceable(story, requirements_by_id)) / story_count
        if story_count else 1.0
    )
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
        ac_quality = supported / len(criteria)

    duplicate_risk = len(find_duplicate_story_ids(stories)) / story_count if story_count else 0.0
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
