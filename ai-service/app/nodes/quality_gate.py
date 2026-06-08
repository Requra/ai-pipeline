from typing import List, Dict
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import (
    ClassifiedRequirement,
    UserStory,
    RequirementCoverage,
    QualityIssue,
)


async def quality_gate_node(state: PipelineState) -> dict:
    """Validate requirements, stories, and coverage. Produce QualityIssue records and adjust status.

    - Adds QualityIssue items for discovered problems.
    - Sets status to 'needs_review' if any high severity issues exist.
    """
    print("--- QUALITY GATE NODE ---")

    reqs: List[ClassifiedRequirement] = state.get("classified_requirements", [])
    stories: List[UserStory] = state.get("user_stories", [])
    coverages: List[RequirementCoverage] = state.get("requirement_coverages", [])

    existing_q = state.get("quality_issues", []) or []

    # Index for lookup
    req_ids = {r.id for r in reqs}
    story_ids = {s.id for s in stories}

    new_issues: List[QualityIssue] = []

    # Validate requirements
    for r in reqs:
        if not getattr(r, "text", "").strip():
            new_issues.append(QualityIssue(
                item_id=r.id,
                item_type="requirement",
                severity="high",
                rule_violated="requirement_empty_text",
                details="Requirement text is empty"
            ))
            r.needs_review = True

        labels = getattr(r, "labels", [])
        if not labels:
            new_issues.append(QualityIssue(
                item_id=r.id,
                item_type="requirement",
                severity="high",
                rule_violated="requirement_missing_labels",
                details="Requirement has no labels assigned"
            ))
            r.needs_review = True

        conf = getattr(r, "classification_confidence", None)
        if conf is None:
            conf = getattr(r, "confidence", None)
        if conf is None or not (0.0 <= conf <= 1.0):
            new_issues.append(QualityIssue(
                item_id=r.id,
                item_type="requirement",
                severity="medium",
                rule_violated="requirement_confidence_invalid",
                details=f"Invalid confidence value: {conf}"
            ))

        # Evidence checks (unless Open Question/Assumption/Out-of-Scope)
        type_labels = set(getattr(r, "candidate_labels", []))
        exempt = type_labels & {"Open Question", "Assumption", "Out-of-Scope"}
        if not exempt:
            evid = getattr(r, "evidence", []) or []
            if not evid:
                new_issues.append(QualityIssue(
                    item_id=r.id,
                    item_type="requirement",
                    severity="high",
                    rule_violated="missing_evidence",
                    details="Requirement missing evidence"
                ))
                r.needs_review = True

    # Validate stories
    for s in stories:
        if not getattr(s, "title", "").strip():
            new_issues.append(QualityIssue(
                item_id=0,
                item_type="story",
                severity="medium",
                rule_violated="story_empty_title",
                details=f"Story {s.id} has empty title"
            ))

        desc = getattr(s, "description", "") or ""
        if "As a" not in desc:
            new_issues.append(QualityIssue(
                item_id=0,
                item_type="story",
                severity="medium",
                rule_violated="story_description_shape",
                details=f"Story {s.id} description does not follow Agile 'As a' pattern"
            ))

        ac = getattr(s, "acceptance_criteria", []) or []
        if not ac:
            new_issues.append(QualityIssue(
                item_id=0,
                item_type="story",
                severity="medium",
                rule_violated="story_missing_acceptance",
                details=f"Story {s.id} missing acceptance criteria"
            ))

        src_ids = getattr(s, "source_requirement_ids", []) or []
        if not src_ids:
            new_issues.append(QualityIssue(
                item_id=0,
                item_type="story",
                severity="high",
                rule_violated="story_missing_source_ids",
                details=f"Story {s.id} missing source_requirement_ids"
            ))

    # Validate coverage mapping
    for c in coverages:
        for sid in c.story_ids:
            if sid not in story_ids:
                new_issues.append(QualityIssue(
                    item_id=0,
                    item_type="coverage",
                    severity="medium",
                    rule_violated="coverage_bad_story_id",
                    details=f"Coverage references missing story id {sid}"
                ))
        if c.requirement_id not in req_ids:
            new_issues.append(QualityIssue(
                item_id=c.requirement_id,
                item_type="coverage",
                severity="medium",
                rule_violated="coverage_bad_requirement_id",
                details=f"Coverage references missing requirement id {c.requirement_id}"
            ))

    # Determine overall status
    has_high = any(q.severity == "high" for q in (existing_q + new_issues))
    status = "needs_review" if has_high else state.get("status", "partial")

    # Return only new issues to avoid duplicates; pipeline reducers will append
    return {"quality_issues": new_issues, "status": status}
