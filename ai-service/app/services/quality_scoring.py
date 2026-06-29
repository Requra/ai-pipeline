"""
Derived quality scoring for a pipeline run.

All scores are computed from real signals already present on the requirements,
stories, and quality issues — nothing here is faked or hard-coded. Scores are in
[0, 1] where higher is better (except ``duplicate_risk`` where lower is better).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Sequence

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
    """Per-requirement groundedness in [0, 1].

    Prefers the retrieval-derived quote_support_score; otherwise falls back to
    "has evidence?" so the score is still meaningful without retrieval.
    """
    qss = getattr(req, "quote_support_score", None)
    if qss is not None:
        try:
            return max(0.0, min(1.0, float(qss)))
        except (TypeError, ValueError):
            pass
    evidence = getattr(req, "evidence", None) or []
    return 1.0 if evidence else 0.0


def _story_is_complete(story) -> bool:
    title = (getattr(story, "title", "") or "").strip()
    acs = getattr(story, "acceptance_criteria", []) or []
    src = getattr(story, "source_requirement_ids", []) or []
    return bool(title) and len(acs) >= 2 and bool(src)


def _severity(issue) -> str:
    if isinstance(issue, dict):
        return issue.get("severity", "")
    return getattr(issue, "severity", "")


def compute_quality_scores(
    requirements: Sequence,
    stories: Sequence,
    quality_issues: Sequence,
) -> QualityScores:
    req_count = len(requirements)
    story_count = len(stories)

    # Groundedness: average per-requirement grounding.
    if req_count:
        groundedness = sum(_req_groundedness(r) for r in requirements) / req_count
    else:
        groundedness = 1.0

    # Traceability: fraction of stories that cite a source requirement.
    if story_count:
        traced = sum(1 for s in stories if (getattr(s, "source_requirement_ids", []) or []))
        traceability = traced / story_count
    else:
        traceability = 1.0

    # Story completeness: fraction of stories that are well-formed.
    if story_count:
        complete = sum(1 for s in stories if _story_is_complete(s))
        completeness = complete / story_count
    else:
        completeness = 1.0

    # Acceptance-criteria quality: fraction of non-generic criteria.
    total_acs = sum(len(getattr(s, "acceptance_criteria", []) or []) for s in stories)
    if not story_count:
        ac_quality = 1.0
    elif total_acs == 0:
        ac_quality = 0.0
    else:
        non_generic = sum(
            1
            for s in stories
            for ac in (getattr(s, "acceptance_criteria", []) or [])
            if not is_generic_ac(getattr(ac, "text", ""))
        )
        ac_quality = non_generic / total_acs

    # Duplicate risk: fraction of stories that duplicate an earlier story.
    if story_count:
        duplicate_risk = len(find_duplicate_story_ids(stories)) / story_count
    else:
        duplicate_risk = 0.0

    high_count = sum(1 for q in quality_issues if _severity(q) == "high")

    components: List[float] = [
        traceability,
        groundedness,
        completeness,
        ac_quality,
        1.0 - duplicate_risk,
    ]
    overall = sum(components) / len(components)

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
