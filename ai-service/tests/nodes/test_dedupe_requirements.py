"""Phase 4 — requirement deduplication."""

from __future__ import annotations

import pytest

from app.nodes.dedupe_requirements import dedupe_requirements_node
from app.schemas.items import EvidenceSpan, ExtractedRequirement


def _req(rid, text, *, actor=None, goal=None, confidence=0.8, labels=None,
         priority="Medium", evidence=None):
    return ExtractedRequirement(
        id=rid,
        text=text,
        actor=actor,
        goal=goal,
        candidate_labels=labels or ["FR"],
        confidence=confidence,
        priority=priority,
        evidence=evidence or [EvidenceSpan(chunk_id=f"c{rid}", quote=text[:20])],
    )


def _state(reqs):
    return {"job_id": "dedupe-job", "extracted_requirements": reqs, "warnings": []}


@pytest.mark.asyncio
async def test_exact_duplicates_merge():
    reqs = [
        _req(1, "The system shall export invoices to PDF."),
        _req(2, "The system shall export invoices to PDF."),
        _req(3, "Users can reset their password by email."),
    ]
    out = await dedupe_requirements_node(_state(reqs))
    deduped = out["extracted_requirements"]
    assert len(deduped) == 2
    assert [r.id for r in deduped] == [1, 2]  # ids reassigned cleanly
    assert any(w.code == "DUPLICATE_REQUIREMENT_MERGED" for w in out["warnings"])


@pytest.mark.asyncio
async def test_near_duplicates_merge():
    reqs = [
        _req(1, "The system must export monthly invoices to a PDF file."),
        _req(2, "The system must export the monthly invoices into a PDF file."),
    ]
    out = await dedupe_requirements_node(_state(reqs))
    assert len(out["extracted_requirements"]) == 1


@pytest.mark.asyncio
async def test_different_actor_same_feature_not_merged():
    reqs = [
        _req(1, "The user can export invoices to PDF.", actor="customer"),
        _req(2, "The user can export invoices to PDF.", actor="administrator"),
    ]
    out = await dedupe_requirements_node(_state(reqs))
    # Different actors → kept separate and flagged.
    assert len(out["extracted_requirements"]) == 2
    assert any(w.code == "POSSIBLE_DUPLICATE_REVIEW" for w in out["warnings"])
    assert any(r.needs_review for r in out["extracted_requirements"])


@pytest.mark.asyncio
async def test_plural_actor_difference_still_merges():
    # "user" vs "users" is not a material actor conflict.
    reqs = [
        _req(1, "The user can export invoices to PDF.", actor="user"),
        _req(2, "The user can export invoices to PDF.", actor="users"),
    ]
    out = await dedupe_requirements_node(_state(reqs))
    assert len(out["extracted_requirements"]) == 1


@pytest.mark.asyncio
async def test_evidence_preserved_and_unioned_after_merge():
    r1 = _req(1, "The system shall archive logs nightly.",
              evidence=[EvidenceSpan(chunk_id="c1", quote="archive logs nightly")])
    r2 = _req(2, "The system shall archive logs nightly.",
              evidence=[EvidenceSpan(chunk_id="c2", quote="archive logs every night")])
    out = await dedupe_requirements_node(_state([r1, r2]))
    deduped = out["extracted_requirements"]
    assert len(deduped) == 1
    quotes = {(e.chunk_id, e.quote) for e in deduped[0].evidence}
    assert ("c1", "archive logs nightly") in quotes
    assert ("c2", "archive logs every night") in quotes


@pytest.mark.asyncio
async def test_merge_preserves_highest_confidence_and_priority():
    r1 = _req(1, "The system shall back up the database.", confidence=0.6, priority="Medium")
    r2 = _req(2, "The system shall back up the database.", confidence=0.95, priority="Critical")
    out = await dedupe_requirements_node(_state([r1, r2]))
    merged = out["extracted_requirements"][0]
    assert merged.confidence == 0.95
    assert merged.priority == "Critical"


@pytest.mark.asyncio
async def test_merge_unions_labels():
    r1 = _req(1, "The system shall encrypt stored data.", labels=["NFR"])
    r2 = _req(2, "The system shall encrypt stored data.", labels=["Constraint"])
    out = await dedupe_requirements_node(_state([r1, r2]))
    merged = out["extracted_requirements"][0]
    assert set(merged.candidate_labels) == {"NFR", "Constraint"}


@pytest.mark.asyncio
async def test_legacy_projection_refreshed():
    reqs = [
        _req(1, "The system shall send emails.", labels=["FR"]),
        _req(2, "The system shall send emails.", labels=["FR"]),
    ]
    out = await dedupe_requirements_node(_state(reqs))
    assert len(out["functional_requirements"]) == 1
    assert out["functional_requirements"][0].id == 1


@pytest.mark.asyncio
async def test_single_requirement_is_noop():
    out = await dedupe_requirements_node(_state([_req(1, "Only one requirement.")]))
    assert out == {}


@pytest.mark.asyncio
async def test_distinct_requirements_not_merged():
    reqs = [
        _req(1, "The system shall export invoices to PDF."),
        _req(2, "Users authenticate with two-factor authentication."),
        _req(3, "The dashboard refreshes analytics every five minutes."),
    ]
    out = await dedupe_requirements_node(_state(reqs))
    assert len(out["extracted_requirements"]) == 3
    # No merge warning when nothing merged.
    assert not any(w.code == "DUPLICATE_REQUIREMENT_MERGED" for w in out.get("warnings", []))
