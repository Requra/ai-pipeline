"""Honest, source-aware quality scoring for the unchanged V1 contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Sequence

from app.services.semantic_quality import (
    clause_coverage,
    has_polarity_conflict,
    introduces_unsupported_approval_outcome,
    is_substantive,
    meaningful_tokens,
    MIN_STORY_ALIGNMENT,
    proposition_support,
    source_fact_texts,
    story_alignment,
    unsupported_fact_terms,
    unsupported_numeric_claims,
)
from app.validators.story_validator import (
    find_duplicate_acceptance_criterion_ids,
    find_duplicate_story_ids,
    is_generic_ac,
)


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
    ) >= 1 and bool(getattr(story, "source_requirement_ids", []) or [])


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
    story_text = " ".join([
        getattr(story, "title", "") or "",
        getattr(story, "description", "") or "",
        *[
            getattr(criterion, "text", "") or ""
            for criterion in (getattr(story, "acceptance_criteria", []) or [])
        ],
    ])
    sources = source_fact_texts(linked)
    return (
        story_alignment(req_texts, story_text) >= MIN_STORY_ALIGNMENT
        and not unsupported_fact_terms(story_text, sources)
        and not has_polarity_conflict(story_text, sources)
    )


_ROOT_CAUSE_MAP = {
    # Groundedness
    "missing_evidence": "EVIDENCE_NOT_GROUNDED",
    "missing_verified_evidence": "EVIDENCE_NOT_GROUNDED",
    "evidence_semantic_mismatch": "EVIDENCE_NOT_GROUNDED",
    "evidence_not_grounded": "EVIDENCE_NOT_GROUNDED",
    "evidence_chunk_mismatch": "EVIDENCE_NOT_GROUNDED",
    "evidence_document_mismatch": "EVIDENCE_NOT_GROUNDED",
    "evidence_low_transcription_confidence": "EVIDENCE_NOT_GROUNDED",
    # Duplicate risk
    "duplicate_requirement": "DUPLICATE_CONTENT",
    "duplicate_story": "DUPLICATE_CONTENT",
    "semantic_conflict_duplicate": "DUPLICATE_CONTENT",
    # Acceptance-criteria quality
    "acceptance_criterion_unsupported_fact": "AC_QUALITY",
    "acceptance_criterion_not_source_aligned": "AC_QUALITY",
    "acceptance_criteria_missing_source_clause": "AC_QUALITY",
    "generic_acceptance_criteria": "AC_QUALITY",
    "duplicate_acceptance_criterion": "AC_QUALITY",
    # Story traceability
    "incorrect_story_requirement_mapping": "STORY_TRACEABILITY",
    "story_missing_source_ids": "STORY_TRACEABILITY",
    "story_unsupported_fact": "STORY_TRACEABILITY",
    # Story completeness
    "story_empty_title": "STORY_COMPLETENESS",
    "story_missing_acceptance": "STORY_COMPLETENESS",
}

_COMPONENT_OWNED_ROOT_CAUSES = {
    "EVIDENCE_NOT_GROUNDED",
    "DUPLICATE_CONTENT",
    "AC_QUALITY",
    "STORY_TRACEABILITY",
    "STORY_COMPLETENESS",
}

_DIAGNOSTIC_ROOT_CAUSES = {
    "priority_not_source_supported",
    "requirement_confidence_invalid",
    "low_confidence_classification",
    "requirement_missing_labels",
    "non_human_story_persona",
    "story_description_shape",
    "story_missing_evidence_reference",
    "story_behavior_needs_review",
    "coverage_bad_story_id",
    "coverage_bad_requirement_id",
    "out_of_scope_covered_by_story",
    "open_question_covered_by_story",
}


def _issue_value(issue, field: str, default=None):
    return issue.get(field, default) if isinstance(issue, dict) else getattr(issue, field, default)


def normalize_issue_root_cause(rule: str) -> str:
    """Return the stable defect family used by scoring and public formatting."""
    normalized = str(rule or "")
    if "complementary" in normalized.lower():
        return "COMPLEMENTARY"
    return _ROOT_CAUSE_MAP.get(normalized, normalized)


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
    if (
        unsupported_fact_terms(text, sources)
        or has_polarity_conflict(text, sources)
        or introduces_unsupported_approval_outcome(text, sources)
    ):
        return False
    return max(
        (proposition_support(source, text) for source in substantive),
        default=0.0,
    ) >= 0.15


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

    if req_count:
        groundedness = sum(_req_groundedness(req) for req in requirements) / req_count
    else:
        groundedness = 0.0

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
        verified_evidence_coverage = (
            sum(
                1
                for req_id in actionable_ids
                if (getattr(requirements_by_id[req_id], "evidence", None) or [])
            ) / len(actionable_ids)
            if actionable_ids else 1.0
        )
        # End-to-end traceability is only as strong as its weakest link: a
        # valid story mapping, actionable requirement coverage, and a verified
        # source reference must all exist. Averaging could report a high value
        # even when one link is completely absent.
        traceability = min(
            mapping_precision,
            requirement_coverage,
            verified_evidence_coverage,
        )
        completeness = sum(1 for story in stories if _story_is_complete(story)) / story_count
    else:
        traceability = 0.0
        completeness = 0.0

    criteria = [ac for story in stories for ac in (getattr(story, "acceptance_criteria", []) or [])]
    if not story_count:
        ac_quality = 0.0
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
        duplicate_criterion_count = sum(
            len(find_duplicate_acceptance_criterion_ids(
                story,
                [
                    requirements_by_id[rid]
                    for rid in (getattr(story, "source_requirement_ids", []) or [])
                    if rid in requirements_by_id
                ],
            ))
            for story in stories
        )
        criterion_uniqueness = max(
            0.0,
            (len(criteria) - duplicate_criterion_count) / len(criteria),
        )
        ac_quality = min(criterion_precision, fact_coverage, criterion_uniqueness)

    story_duplicate_risk = len(find_duplicate_story_ids(stories)) / story_count if story_count else 0.0
    requirement_duplicate_risk = _duplicate_requirement_count(requirements) / req_count if req_count else 0.0
    duplicate_risk = max(story_duplicate_risk, requirement_duplicate_risk)
    # Group issues by stable entity and normalized root cause.  The public
    # QualityIssue contract stays unchanged; this identity exists only for
    # scoring and prevents aliases from being counted more than once.
    grouped = {}
    for issue in quality_issues:
        rule = _issue_value(issue, "rule_violated", "")
        if not rule:
            rule = _issue_value(issue, "rule", "")
        rule = str(rule or "")
        root_cause = normalize_issue_root_cause(rule)
        item_id = _issue_value(issue, "item_id")
        item_type = str(_issue_value(issue, "item_type", "") or "")
        severity = _issue_value(issue, "severity", "")
        severity = str(severity or "").lower()

        key = (item_type, item_id, root_cause)
        if key not in grouped:
            grouped[key] = {
                "item_id": item_id,
                "item_type": item_type,
                "root_cause": root_cause,
                "severity": severity,
                "rule_violated": rule,
            }
        else:
            # keep highest severity
            existing_sev = grouped[key]["severity"]
            if severity == "high" or (severity == "medium" and existing_sev != "high"):
                grouped[key]["severity"] = severity

    penalizable_high_count = 0
    penalizable_medium_count = 0
    penalizable_low_count = 0

    for (_, _, root_cause), info in grouped.items():
        if root_cause == "COMPLEMENTARY":
            continue
        if root_cause in _DIAGNOSTIC_ROOT_CAUSES:
            continue
        if root_cause not in _COMPONENT_OWNED_ROOT_CAUSES:
            sev = info["severity"]
            if sev == "high":
                penalizable_high_count += 1
            elif sev == "medium":
                penalizable_medium_count += 1
            elif sev == "low":
                penalizable_low_count += 1

    # Reported high severity issues count (includes represented + penalizable user-facing defects, excluding diagnostic/informational)
    high_count = sum(
        1 for info in grouped.values()
        if info["severity"] == "high"
        and info["root_cause"] != "COMPLEMENTARY"
        and info["root_cause"] not in _DIAGNOSTIC_ROOT_CAUSES
    )

    if req_count == 0 and story_count == 0:
        overall = 0.0
    else:
        overall = (
            groundedness * 0.30
            + traceability * 0.25
            + completeness * 0.15
            + ac_quality * 0.20
            + (1.0 - duplicate_risk) * 0.10
        )
        penalty = min(0.70, penalizable_high_count * 0.15 + penalizable_medium_count * 0.05 + penalizable_low_count * 0.01)
        overall = max(0.0, overall - penalty)
        if penalizable_high_count:
            overall = min(overall, 0.59)
        elif penalizable_medium_count:
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
