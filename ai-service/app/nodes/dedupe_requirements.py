"""
dedupe_requirements node.

Chunk overlap, repeated document sections, and LLM repetition all produce the
same requirement more than once. This node merges exact and near-duplicate
requirements *before* classification/generation so downstream nodes operate on a
clean set.

Merge policy (never lose grounding):
  * Exact (normalized-text) or near-duplicate (token Jaccard >= threshold) merge.
  * Union evidence spans (dedup by chunk_id+quote) — evidence is never dropped.
  * Keep the highest confidence and the strongest priority.
  * Union candidate labels; OR needs_review; fill missing actor/goal.
  * Do NOT merge when both requirements name a different actor — same feature for
    a different actor is a distinct requirement (flagged POSSIBLE_DUPLICATE_REVIEW).
  * Reassign stable sequential ids and refresh the legacy projection.
"""

from __future__ import annotations

import re
from typing import List, Optional

from app.nodes.extract import project_legacy_requirements
from app.progress import update_progress
from app.rag.scoring import tokenize
from app.schemas.items import ExtractedRequirement, PipelineWarning
from app.schemas.pipeline_state import PipelineState

# Token-set Jaccard at/above this is considered a near-duplicate.
NEAR_DUPLICATE_THRESHOLD = 0.8

_PRIORITY_RANK = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}


def _normalize_text(text: str) -> str:
    """Canonical form for exact-duplicate detection."""
    if not text:
        return ""
    lowered = re.sub(r"[^a-z0-9\s]+", " ", text.lower())
    return re.sub(r"\s+", " ", lowered).strip()


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _norm_actor(actor: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (actor or "").strip().lower())


def _actors_conflict(a: ExtractedRequirement, b: ExtractedRequirement) -> bool:
    """True only when both name a (different) actor — a real semantic conflict."""
    na, nb = _norm_actor(a.actor), _norm_actor(b.actor)
    if na and nb and na != nb:
        # "user"/"users" style differences should not count as a conflict.
        return not (na.rstrip("s") == nb.rstrip("s"))
    return False


def _higher_priority(p1: str, p2: str) -> str:
    return p1 if _PRIORITY_RANK.get(p1, 1) >= _PRIORITY_RANK.get(p2, 1) else p2


def _merge_into(base: ExtractedRequirement, other: ExtractedRequirement) -> None:
    """Fold ``other`` into ``base`` in place, preserving all grounding."""
    # Evidence union (dedup by chunk_id + quote).
    seen = {(e.chunk_id, e.quote) for e in base.evidence}
    for ev in other.evidence:
        key = (ev.chunk_id, ev.quote)
        if key not in seen:
            base.evidence.append(ev)
            seen.add(key)

    base.confidence = max(base.confidence, other.confidence)
    base.priority = _higher_priority(base.priority, other.priority)

    for label in other.candidate_labels:
        if label not in base.candidate_labels:
            base.candidate_labels.append(label)

    base.actor = base.actor or other.actor
    base.goal = base.goal or other.goal
    if "explicit" in (base.extraction_type, other.extraction_type):
        base.extraction_type = "explicit"
    elif base.extraction_type is None:
        base.extraction_type = other.extraction_type

    base.needs_review = bool(base.needs_review or other.needs_review)
    reasons = [r for r in (base.review_reason, other.review_reason) if r]
    if reasons:
        base.review_reason = " ".join(dict.fromkeys(reasons))


async def dedupe_requirements_node(state: PipelineState) -> dict:
    print("--- DEDUPE REQUIREMENTS NODE ---")
    update_progress(state.get("job_id"), "dedupe_requirements", 55, "PROCESSING")

    reqs: List[ExtractedRequirement] = state.get("extracted_requirements", []) or []
    if len(reqs) <= 1:
        return {}  # nothing to dedupe

    groups: List[dict] = []  # {"base": req, "norm": str, "tokens": set}
    merged_count = 0
    possible_dup_ids: List[int] = []

    for req in reqs:
        norm = _normalize_text(req.text)
        tokens = set(tokenize(req.text))

        best = None
        for group in groups:
            exact = bool(norm) and norm == group["norm"]
            near = _jaccard(tokens, group["tokens"]) >= NEAR_DUPLICATE_THRESHOLD
            if not (exact or near):
                continue
            if _actors_conflict(group["base"], req):
                # Similar text, different actor → keep separate, flag for review.
                req.needs_review = True
                req.review_reason = (req.review_reason or "") + " [POSSIBLE_DUPLICATE: similar to another requirement but with a different actor]"
                possible_dup_ids.append(req.id)
                best = "conflict"
                break
            best = group
            break

        if best is None or best == "conflict":
            groups.append({"base": req, "norm": norm, "tokens": tokens})
            continue

        _merge_into(best["base"], req)
        # Widen the group's token set so transitive duplicates still match.
        best["tokens"] |= tokens
        merged_count += 1

    deduped = [g["base"] for g in groups]
    for new_id, req in enumerate(deduped, start=1):
        req.id = new_id

    legacy = project_legacy_requirements(deduped)

    result: dict = {
        "extracted_requirements": deduped,
        "functional_requirements": legacy,
    }

    new_warnings: List[PipelineWarning] = []
    if merged_count:
        new_warnings.append(PipelineWarning(
            node_name="dedupe_requirements",
            code="DUPLICATE_REQUIREMENT_MERGED",
            message=f"Merged {merged_count} duplicate requirement(s) into canonical entries.",
        ))
    if possible_dup_ids:
        new_warnings.append(PipelineWarning(
            node_name="dedupe_requirements",
            code="POSSIBLE_DUPLICATE_REVIEW",
            message=(
                f"{len(possible_dup_ids)} requirement(s) look similar to others but "
                "differ by actor; kept separate and flagged for review."
            ),
        ))
    if new_warnings:
        result["warnings"] = (state.get("warnings", []) or []) + new_warnings

    return result
