"""Phase 5 — evidence retrieval per requirement."""

from __future__ import annotations

import pytest

from app.nodes.retrieve_evidence import (
    MAX_EVIDENCE_PER_REQ,
    SNIPPET_MAX_CHARS,
    retrieve_evidence_node,
)
from app.rag.source_index import build_source_index, clear_source_index
from app.schemas.items import EvidenceSpan, ExtractedRequirement, SourceChunk


def _chunk(cid, text, idx=0):
    return SourceChunk(chunk_id=cid, text=text, start_char=idx, end_char=idx + len(text))


def _req(rid, text, *, evidence=None, confidence=0.8, actor=None, goal=None):
    return ExtractedRequirement(
        id=rid, text=text, actor=actor, goal=goal,
        candidate_labels=["FR"], confidence=confidence,
        evidence=evidence if evidence is not None else [],
    )


def _state(job_id, reqs, chunks):
    build_source_index(job_id, chunks)
    return {
        "job_id": job_id,
        "source_index_id": job_id,
        "extracted_requirements": reqs,
        "chunks": chunks,
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_retrieves_relevant_evidence_and_preserves_original():
    job_id = "ret-1"
    chunks = [
        _chunk("c0", "The system must export monthly invoices to PDF for the finance team.", 0),
        _chunk("c1", "Users authenticate with two-factor authentication.", 100),
    ]
    original = EvidenceSpan(chunk_id="manual", quote="export monthly invoices")
    req = _req(1, "Export monthly invoices to PDF", evidence=[original], goal="export invoices")
    out = await retrieve_evidence_node(_state(job_id, [req], chunks))

    r = out["extracted_requirements"][0]
    chunk_ids = {e.chunk_id for e in r.evidence}
    assert "manual" in chunk_ids               # original preserved
    assert "c0" in chunk_ids                    # relevant chunk attached
    assert r.evidence_match_score > 0.0
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_snippet_is_capped_no_full_chunk_bloat():
    job_id = "ret-2"
    long_text = "Invoices must be exported. " + ("filler sentence here. " * 80)
    chunks = [_chunk("c0", long_text, 0)]
    req = _req(1, "Export invoices", goal="export invoices")
    out = await retrieve_evidence_node(_state(job_id, [req], chunks))
    r = out["extracted_requirements"][0]
    for ev in r.evidence:
        assert len(ev.quote) <= SNIPPET_MAX_CHARS
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_weak_support_flags_and_lowers_confidence():
    job_id = "ret-3"
    chunks = [_chunk("c0", "Completely unrelated content about gardening and weather.", 0)]
    # Requirement quote not in source, and query won't match the gardening chunk.
    req = _req(1, "The system shall reconcile ledger entries", confidence=0.9,
               evidence=[EvidenceSpan(chunk_id="x", quote="reconcile ledger entries")])
    out = await retrieve_evidence_node(_state(job_id, [req], chunks))
    r = out["extracted_requirements"][0]
    assert r.needs_review is True
    assert r.confidence < 0.9
    assert any(w.code == "WEAK_EVIDENCE_SUPPORT" for w in out["warnings"])
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_evidence_cap_applied():
    job_id = "ret-4"
    # Five distinct chunks all matching the query -> cap evidence per requirement.
    chunks = [_chunk(f"c{i}", f"data export requirement number {i} for reports", i * 50) for i in range(5)]
    # Start with two existing evidence so retrieved additions overflow the cap.
    req = _req(1, "data export requirement for reports",
               evidence=[
                   EvidenceSpan(chunk_id="orig1", quote="data export"),
                   EvidenceSpan(chunk_id="orig2", quote="report data"),
               ])
    out = await retrieve_evidence_node(_state(job_id, [req], chunks))
    r = out["extracted_requirements"][0]
    assert len(r.evidence) <= MAX_EVIDENCE_PER_REQ
    assert any(w.code == "EVIDENCE_LIMIT_APPLIED" for w in out["warnings"])
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_quote_support_score_recorded():
    job_id = "ret-5"
    chunks = [_chunk("c0", "The system shall send weekly digest emails.", 0)]
    req = _req(1, "Send weekly digest emails",
               evidence=[EvidenceSpan(chunk_id="c0", quote="send weekly digest emails")])
    out = await retrieve_evidence_node(_state(job_id, [req], chunks))
    r = out["extracted_requirements"][0]
    # The quote (lowercased form) — check support score is computed in [0,1].
    assert r.quote_support_score is not None
    assert 0.0 <= r.quote_support_score <= 1.0
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_no_index_is_graceful():
    # source_index_id points nowhere -> graceful skip with NO_RETRIEVED_EVIDENCE.
    req = _req(1, "Some requirement", evidence=[EvidenceSpan(chunk_id="c0", quote="x")])
    state = {
        "job_id": "ret-missing",
        "source_index_id": "does-not-exist",
        "extracted_requirements": [req],
        "chunks": [],
        "warnings": [],
    }
    out = await retrieve_evidence_node(state)
    assert any(w.code == "NO_RETRIEVED_EVIDENCE" for w in out["warnings"])
    # Requirement is preserved.
    assert out["extracted_requirements"][0].id == 1


@pytest.mark.asyncio
async def test_empty_requirements_is_noop():
    out = await retrieve_evidence_node({"job_id": "ret-empty", "extracted_requirements": []})
    assert out == {}


@pytest.mark.asyncio
async def test_ambiguous_retrieval_candidate_is_not_promoted_to_evidence():
    job_id = "ret-ambiguous"
    chunks = [
        _chunk(
            "monthly-report",
            "Administrators shall export a monthly operations report.",
        )
    ]
    req = _req(
        1,
        "The application shall allow only administrators to retrieve a retained report.",
    )

    out = await retrieve_evidence_node(_state(job_id, [req], chunks))
    result = out["extracted_requirements"][0]

    assert result.evidence == []
    assert result.needs_review is True
    assert "ambiguous retrieval not promoted" in (result.review_reason or "")
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_retrieval_recovers_better_clause_from_same_audio_chunk():
    """A weak extraction quote must not hide a stronger audio clause."""
    job_id = "ret-audio-same-chunk"
    source = "All communication between clients and servers must be encrypted using T L S 1 3 protocol."
    chunks = [SourceChunk(
        chunk_id="audio-1", text=source, start_char=0, end_char=len(source),
        start_time_sec=0.0, end_time_sec=4.0, document_id="audio-source", language="en",
    )]
    req = _req(
        1,
        "The system shall encrypt all communication between clients and servers using TLS 1.3 protocol.",
        evidence=[EvidenceSpan(chunk_id="audio-1", quote="Communication is secure.")],
    )

    out = await retrieve_evidence_node(_state(job_id, [req], chunks))

    result = out["extracted_requirements"][0]
    assert any("T L S 1 3" in evidence.quote for evidence in result.evidence)
    clear_source_index(job_id)
