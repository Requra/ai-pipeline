"""Phase 4 — requirement deduplication."""

from __future__ import annotations

import pytest

from app.nodes.dedupe_requirements import (
    _conditional_approval_and_quantity_are_composable,
    canonicalize_requirements,
    dedupe_requirements_node,
)
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
async def test_cross_source_paraphrases_without_matching_intent_stay_separate():
    reqs = [
        _req(
            1,
            "The system must export monthly invoices to a PDF file.",
            goal="export finance invoices",
            evidence=[EvidenceSpan(chunk_id="finance", quote="export invoices to PDF", document_id="finance-pdf")],
        ),
        _req(
            2,
            "The system must export the monthly invoices into a PDF file.",
            goal=None,
            evidence=[EvidenceSpan(chunk_id="operations", quote="export invoices to PDF", document_id="operations-audio")],
        ),
    ]

    out = await dedupe_requirements_node(_state(reqs))

    assert len(out["extracted_requirements"]) == 2


@pytest.mark.asyncio
async def test_identical_proposition_merges_despite_inconsistent_actor_fields():
    reqs = [
        _req(1, "The user can export invoices to PDF.", actor="customer"),
        _req(2, "The user can export invoices to PDF.", actor="administrator"),
    ]
    out = await dedupe_requirements_node(_state(reqs))
    assert len(out["extracted_requirements"]) == 1
    assert any(w.code == "DUPLICATE_REQUIREMENT_MERGED" for w in out["warnings"])


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


@pytest.mark.asyncio
async def test_atomic_children_collapse_into_source_level_composite():
    composite = _req(
        1,
        "The system shall send escalation notifications to the primary on-call group and retain delivery outcomes for troubleshooting purposes.",
    )
    notify = _req(2, "The system shall send escalation notifications to the primary on-call group.")
    retain = _req(3, "The system shall retain delivery outcomes for troubleshooting purposes.")

    out = await dedupe_requirements_node(_state([notify, retain, composite]))

    assert len(out["extracted_requirements"]) == 1
    assert out["extracted_requirements"][0].text == composite.text
    assert len(out["extracted_requirements"][0].evidence) == 3


def test_pre_generation_canonicalization_merges_technical_actor_paraphrases():
    first = _req(
        5,
        "The system shall produce CSV and PDF audit reports including the applied filters.",
        actor="System",
        evidence=[EvidenceSpan(chunk_id="c5", quote="produce CSV and PDF reports")],
    )
    second = _req(
        11,
        "The export service shall produce CSV and PDF audit reports including the applied filters.",
        actor="Export service",
        evidence=[EvidenceSpan(chunk_id="c11", quote="reports include applied filters")],
    )

    canonical, merged_count, id_map, _ = canonicalize_requirements([first, second])

    assert merged_count == 1
    assert len(canonical) == 1
    assert id_map == {5: 1, 11: 1}
    assert {e.chunk_id for e in canonical[0].evidence} == {"c5", "c11"}


def test_atomic_or_clause_is_absorbed_by_equivalent_composite():
    atomic = _req(8, "The service shall notify owners when an export is downloaded.")
    composite = _req(
        9,
        "The service shall notify owners when an administrator role is granted or an export is downloaded.",
    )

    canonical, merged_count, _, _ = canonicalize_requirements([atomic, composite])

    assert merged_count == 1
    assert len(canonical) == 1
    assert "administrator role" in canonical[0].text


def test_contained_action_with_purpose_clause_merges_into_composite_rule():
    composite = _req(
        1,
        "Asset database records shall not be permanently deleted; instead, they must be soft-deleted and marked as Retired.",
    )
    atomic = _req(
        2,
        "The system must soft-delete and mark records as Retired for audit compliance.",
    )

    canonical, merged_count, _, _ = canonicalize_requirements([composite, atomic])

    assert merged_count == 1
    assert len(canonical) == 1
    assert "not be permanently deleted" in canonical[0].text


@pytest.mark.parametrize(
    ("approval", "quantity"),
    [
        (
            "Standard checkout requests require manager approval when the asset value exceeds $1,000.",
            "Standard users may check out up to three assets simultaneously.",
        ),
        (
            "تتطلب الطلبات موافقة المدير إذا تجاوزت القيمة 1000.",
            "يسمح للمستخدمين بسحب حتى ثلاثة أصول في الوقت نفسه.",
        ),
    ],
)
def test_conditional_approval_and_quantity_limits_are_complementary(approval, quantity):
    assert _conditional_approval_and_quantity_are_composable(
        _req(1, approval), _req(2, quantity)
    )


def test_explicit_approval_exemption_is_not_silently_marked_complementary():
    assert not _conditional_approval_and_quantity_are_composable(
        _req(1, "Checkout requests over $1,000 require manager approval."),
        _req(2, "Requests of up to three assets are exempt from approval."),
    )


@pytest.mark.asyncio
async def test_two_document_golden_canonical_count_is_sixteen():
    """Regression for the uploaded DOCX/PDF fixture's 27 raw extractions."""
    workspace = [
        "The customer workspace shall allow an account owner to create a project and invite named collaborators with a project-scoped role.",
        "The service shall issue a single-use email invitation link that expires after twenty-four hours and records its redemption time.",
        "The workspace shall require multi-factor authentication for administrators before they can change organization settings or billing contacts.",
        "The system shall record immutable audit events for invitation creation, role changes, sign-in failures, and export requests.",
        "The audit search screen shall filter by actor, action, target project, and a caller-selected date range.",
        "The export service shall produce CSV and PDF audit reports and include the applied filters in each generated artifact.",
        "The application shall retain exported reports for thirty days and allow only administrators to retrieve a retained report.",
        "The notification service shall alert account owners when a new administrator role is granted or when an export is downloaded.",
    ]
    operations = [
        "The operations portal shall display a queue of pending support cases and allow an assigned analyst to change case status with a reason.",
        "Each status transition shall preserve the prior value, the acting user, the timestamp, and a human-readable rationale in case history.",
        "The portal shall enforce a four-hour response target for high-priority cases and display a warning when less than one hour remains.",
        "Supervisors shall configure escalation rules by customer tier, business hours calendar, and unresolved case age.",
        "The reporting view shall summarize response-time compliance by team, priority, and calendar month without exposing customer credentials.",
        "Administrators shall export a monthly operations report and the report shall identify its source period and generation timestamp.",
        "The system shall send escalation notifications to the primary on-call group and retain delivery outcomes for troubleshooting.",
        "Analysts shall attach sanitized diagnostic files to a case; attachments shall be virus-scanned before they become available to other users.",
    ]
    repeated_workspace = [workspace[2], workspace[4], workspace[5], workspace[6], workspace[7]]
    atomic_operations = [
        "The system shall enforce a four-hour response target for high-priority cases.",
        "The system shall display a warning when less than one hour remains to meet the response target for high-priority cases.",
        "The system shall send escalation notifications to the primary on-call group.",
        "The system shall retain delivery outcomes for troubleshooting purposes.",
        "Analysts shall be able to attach sanitized diagnostic files to a case.",
        "The system shall virus-scan attachments before making them available to other users.",
    ]
    raw = workspace + repeated_workspace + operations[:4] + atomic_operations + operations[4:]
    reqs = [_req(index, text, actor="System") for index, text in enumerate(raw, start=1)]

    out = await dedupe_requirements_node(_state(reqs))

    assert len(raw) == 27
    assert len(out["extracted_requirements"]) == 16
