"""Cross-domain semantic integrity and provenance test suite.

Covers:
1. Multi-domain disposition classification & gating (Fintech, Healthcare, Logistics).
2. Active negative constraints (disposition='accepted', BR/Constraint) generating compliant stories.
3. Rejected proposals (disposition='rejected') & deferred features (disposition='deferred') strictly gated.
4. Proposed / uncertain items gated with needs_review coverage.
5. Invariant verification: Zero ghost IDs in warnings across all domains.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from app.graph.pipeline import build_pipeline
from app.schemas.items import JobResult
from app.services.source_processing import audio, extractors
from app.nodes import ingest, extract, classify, generate, summarize, dedupe_requirements, repair_stories
from app.rag import embeddings


class FakeEmbedder:
    model = "fake"
    async def embed_documents(self, texts):
        return [[0.1] * 1536 for _ in texts]
    async def embed_query(self, text):
        return [0.1] * 1536


@pytest.fixture
def mock_oracle_env(monkeypatch):
    embeddings.set_embedder(FakeEmbedder())
    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "get_audio_duration_seconds", lambda *args, **kwargs: 30.0)


@pytest.mark.asyncio
async def test_fintech_semantic_disposition_oracle(monkeypatch, mock_oracle_env):
    """Test Fintech domain: invoice immutability (accepted negative constraint) vs crypto (rejected) vs multi-currency (deferred)."""
    async def fake_llm_ainvoke(messages, **kwargs):
        system = messages[0][1] if isinstance(messages, list) and len(messages) > 0 else ""

        if "gatekeeper" in system.lower() or "relevance" in system.lower():
            return MagicMock(content=json.dumps({"is_useful": True, "relevance_score": 0.98, "reason": "valid fintech spec"}))

        if "Extract atomic software requirements" in system or "Extract requirements" in system:
            return MagicMock(content=json.dumps({
                "requirements": [
                    {
                        "id": 1,
                        "text": "Issued invoices cannot be deleted under any circumstances; they must be archived with immutable audit metadata.",
                        "actor": "Billing System",
                        "goal": "Prevent deletion and enforce archiving of issued invoices",
                        "disposition": "accepted",
                        "candidate_labels": ["BR", "Constraint"],
                        "confidence": 0.98,
                        "priority": "Critical",
                        "evidence": [{"chunk_id": "c1", "quote": "Issued invoices cannot be deleted under any circumstances; they must be archived with immutable audit metadata."}]
                    },
                    {
                        "id": 2,
                        "text": "The platform should support automated cryptocurrency payment conversion on checkout.",
                        "actor": "Checkout System",
                        "goal": "Automate crypto payment conversion",
                        "disposition": "rejected",
                        "candidate_labels": ["Out-of-Scope"],
                        "confidence": 0.95,
                        "priority": "Low",
                        "evidence": [{"chunk_id": "c1", "quote": "We considered automated cryptocurrency conversion, but decided against it due to regulatory compliance."}]
                    },
                    {
                        "id": 3,
                        "text": "The platform shall support multi-currency billing and exchange rate conversion.",
                        "actor": "Billing System",
                        "goal": "Support multi-currency billing",
                        "disposition": "deferred",
                        "candidate_labels": ["Out-of-Scope"],
                        "confidence": 0.92,
                        "priority": "Low",
                        "evidence": [{"chunk_id": "c1", "quote": "Multi-currency invoicing is deferred to phase 2 next fiscal year."}]
                    },
                ]
            }))

        if "You classify each requirement" in system:
            return MagicMock(content=json.dumps({
                "classifications": [
                    {"id": 1, "labels": ["BR"], "confidence": 0.98},
                    {"id": 2, "labels": ["FR"], "confidence": 0.95},
                    {"id": 3, "labels": ["FR"], "confidence": 0.92},
                ]
            }))

        if "Convert requirements into USER STORIES" in system or "user stories" in system.lower():
            # Only called for requirement 1
            return MagicMock(content=json.dumps({
                "stories": [
                    {
                        "id": "fintech-sem-1_story_1",
                        "source_requirement_ids": [1],
                        "title": "Immutable Invoice Archival Policy",
                        "description": "As a finance auditor, I want to prevent deletion and enforce archiving of issued invoices, so that statutory audit compliance is guaranteed.",
                        "acceptance_criteria": [
                            "Given an issued invoice, when a user attempts to delete it, then the system blocks the deletion request.",
                            "Given an issued invoice, when inspecting audit logs, then immutable retention metadata is displayed."
                        ],
                        "labels": ["BR", "Constraint"],
                        "story_points": 3
                    }
                ]
            }))

        if "conflict" in system.lower():
            return MagicMock(content=json.dumps({"conflicts": []}))

        return MagicMock(content=json.dumps({
            "executive_summary": "Fintech billing specification.",
            "scope": ["Immutable invoice archival"],
            "out_of_scope": ["Automated cryptocurrency payment conversion (rejected)", "Multi-currency billing (deferred)"],
            "key_decisions": ["Block invoice deletion; enforce immutable archival"],
            "open_questions": []
        }))

    mock_llm_client = MagicMock()
    mock_llm_client.ainvoke = fake_llm_ainvoke

    from app import llm
    monkeypatch.setattr(llm, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extract, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(dedupe_requirements, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(classify, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(generate, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(summarize, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(repair_stories, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(ingest, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extractors, "get_llm", lambda: mock_llm_client)

    compiled_graph = build_pipeline()
    doc_text = "Issued invoices cannot be deleted under any circumstances; they must be archived with immutable audit metadata. We considered automated cryptocurrency conversion, but decided against it due to regulatory compliance. Multi-currency invoicing is deferred to phase 2 next fiscal year."
    initial_state = {
        "job_id": "fintech-sem-1",
        "tenant_id": "ten-fin",
        "project_id": "proj-fin",
        "file_type": "text",
        "raw_text": doc_text,
        "language": "en",
        "raw_inputs": [{"document_id": "doc_fin", "filename": "fintech_spec.txt", "file_type": "text", "raw_text": doc_text}],
        "source_documents": [{"document_id": "doc_fin", "filename": "fintech_spec.txt", "file_type": "text"}],
        "chunks": [],
        "source_index_id": None,
        "retrieval_stats": None,
        "pii_stats": None,
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "quality_report": None,
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.98,
        "status": "started",
        "error": None,
        "started_at": 0.0,
        "processing_time_ms": 0,
        "repair_attempts": 0,
        "resolved_quality_issues": [],
        "functional_requirements": [],
        "processed_sources": None,
        "source_processing_stats": None,
        "partial_source_failure": False,
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    job_result: JobResult = final_state.get("job_result")

    assert job_result is not None
    assert job_result.status == "completed", f"FAILED STATUS: {job_result.status}, ISSUES: {final_state.get('quality_issues')}, WARNINGS: {final_state.get('warnings')}"
    assert len(job_result.requirements) == 3

    # Story & Export Verification
    assert len(job_result.user_stories) == 1
    assert job_result.user_stories[0].requirement_id == "REQ-001"
    assert "invoice" in job_result.user_stories[0].title.lower()

    # Coverages
    cov_by_req = {c.requirement_id: c for c in job_result.requirement_coverages}
    assert cov_by_req["REQ-001"].coverage_type == "covered_by_story"
    assert cov_by_req["REQ-002"].coverage_type == "non_story"
    assert cov_by_req["REQ-003"].coverage_type == "non_story"

    # Exports
    assert len(job_result.exports.excel.rows) == 1
    assert job_result.exports.excel.rows[0]["requirement_id"] == "REQ-001"
    assert len(job_result.exports.jira.rows) == 1

    # Ghost ID Check
    valid_req_ids = {r.id for r in job_result.requirements}
    for warning in job_result.warnings:
        w_msg = warning.message if hasattr(warning, "message") else warning.get("message", "")
        for token in w_msg.split():
            clean_token = token.strip(".,;:()[]")
            if clean_token.startswith("REQ-"):
                assert clean_token in valid_req_ids, f"Ghost requirement ID '{clean_token}' in warning: '{w_msg}'"


@pytest.mark.asyncio
async def test_healthcare_uncertain_proposal_gating(monkeypatch, mock_oracle_env):
    """Test Healthcare domain: explicit vital trends (accepted) vs unencrypted export (accepted prohibition) vs uncertain AI diagnosis (proposed)."""
    async def fake_llm_ainvoke(messages, **kwargs):
        system = messages[0][1] if isinstance(messages, list) and len(messages) > 0 else ""

        if "gatekeeper" in system.lower() or "relevance" in system.lower():
            return MagicMock(content=json.dumps({"is_useful": True, "relevance_score": 0.98, "reason": "valid healthcare spec"}))

        if "Extract atomic software requirements" in system or "Extract requirements" in system:
            return MagicMock(content=json.dumps({
                "requirements": [
                    {
                        "id": 1,
                        "text": "Clinicians shall view patient vital sign trends over 24-hour windows.",
                        "actor": "Clinician",
                        "goal": "View 24-hour vital trends",
                        "disposition": "accepted",
                        "candidate_labels": ["FR"],
                        "confidence": 0.98,
                        "priority": "High",
                        "evidence": [{"chunk_id": "c1", "quote": "Clinicians shall view patient vital sign trends over 24-hour windows."}]
                    },
                    {
                        "id": 2,
                        "text": "The EHR must block exporting patient medical records to unencrypted USB drives.",
                        "actor": "EHR System",
                        "goal": "Block unencrypted USB export",
                        "disposition": "accepted",
                        "candidate_labels": ["BR", "Constraint"],
                        "confidence": 0.96,
                        "priority": "Critical",
                        "evidence": [{"chunk_id": "c1", "quote": "Under no circumstances should patient medical records be exported to unencrypted USB drives."}]
                    },
                    {
                        "id": 3,
                        "text": "The platform might offer automated AI diagnostic suggestions in future releases pending clinical trial approval.",
                        "actor": "System",
                        "goal": "AI diagnostic suggestions",
                        "disposition": "proposed",
                        "candidate_labels": ["FR"],
                        "confidence": 0.75,
                        "priority": "Low",
                        "evidence": [{"chunk_id": "c1", "quote": "Maybe we can add AI diagnostic suggestions in the future if clinical trial approval is granted."}]
                    },
                ]
            }))

        if "You classify each requirement" in system:
            return MagicMock(content=json.dumps({
                "classifications": [
                    {"id": 1, "labels": ["FR"], "confidence": 0.98},
                    {"id": 2, "labels": ["BR"], "confidence": 0.96},
                    {"id": 3, "labels": ["FR"], "confidence": 0.75},
                ]
            }))

        if "Convert requirements into USER STORIES" in system or "user stories" in system.lower():
            return MagicMock(content=json.dumps({
                "stories": [
                    {
                        "id": "health-sem-1_story_1",
                        "source_requirement_ids": [1],
                        "title": "Clinician 24-Hour Vital Sign Trends",
                        "description": "As a clinician, I want to view patient vital sign trends over 24-hour windows, so that patient condition trajectory is monitored.",
                        "acceptance_criteria": [
                            "Given an authorized clinician viewing patient chart, when selecting the 24-hour window, then vital trends are displayed."
                        ],
                        "labels": ["FR"],
                        "story_points": 3
                    },
                    {
                        "id": "health-sem-1_story_2",
                        "source_requirement_ids": [2],
                        "title": "Enforce USB Export Encryption Lockout",
                        "description": "As a compliance officer, I want to block exporting records to unencrypted USB drives, so that HIPAA data security is preserved.",
                        "acceptance_criteria": [
                            "Given an unencrypted USB drive, when an export is attempted, then the system blocks the operation.",
                            "Given an unauthorized export attempt, when the event occurs, then a security event is recorded in the audit log."
                        ],
                        "labels": ["BR", "Constraint"],
                        "story_points": 2
                    }
                ]
            }))

        if "conflict" in system.lower():
            return MagicMock(content=json.dumps({"conflicts": []}))

        return MagicMock(content=json.dumps({
            "executive_summary": "Healthcare EHR clinical system specification.",
            "scope": ["24-hour vital sign trends", "USB export encryption policy"],
            "out_of_scope": [],
            "key_decisions": ["Block unencrypted USB exports"],
            "open_questions": ["AI diagnostic suggestions pending clinical trial approval"]
        }))

    mock_llm_client = MagicMock()
    mock_llm_client.ainvoke = fake_llm_ainvoke

    from app import llm
    monkeypatch.setattr(llm, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extract, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(dedupe_requirements, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(classify, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(generate, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(summarize, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(repair_stories, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(ingest, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extractors, "get_llm", lambda: mock_llm_client)

    compiled_graph = build_pipeline()
    doc_text = "Clinicians shall view patient vital sign trends over 24-hour windows. Under no circumstances should patient medical records be exported to unencrypted USB drives. Maybe we can add AI diagnostic suggestions in the future if clinical trial approval is granted."
    initial_state = {
        "job_id": "health-sem-1",
        "tenant_id": "ten-health",
        "project_id": "proj-health",
        "file_type": "text",
        "raw_text": doc_text,
        "language": "en",
        "raw_inputs": [{"document_id": "doc_health", "filename": "health_spec.txt", "file_type": "text", "raw_text": doc_text}],
        "source_documents": [{"document_id": "doc_health", "filename": "health_spec.txt", "file_type": "text"}],
        "chunks": [],
        "source_index_id": None,
        "retrieval_stats": None,
        "pii_stats": None,
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "quality_report": None,
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.98,
        "status": "started",
        "error": None,
        "started_at": 0.0,
        "processing_time_ms": 0,
        "repair_attempts": 0,
        "resolved_quality_issues": [],
        "functional_requirements": [],
        "processed_sources": None,
        "source_processing_stats": None,
        "partial_source_failure": False,
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    job_result: JobResult = final_state.get("job_result")

    assert job_result is not None
    assert job_result.status == "completed", f"FAILED HEALTH STATUS: {job_result.status}, ISSUES: {final_state.get('quality_issues')}, WARNINGS: {final_state.get('warnings')}"

    # 1. Verify requirements
    assert len(job_result.requirements) == 3

    # 2. Verify Stories (only actionable 1 and 2, proposed requirement 3 is gated)
    assert len(job_result.user_stories) == 2
    story_req_ids = {s.requirement_id for s in job_result.user_stories}
    assert story_req_ids == {"REQ-001", "REQ-002"}

    # 3. Verify Requirement Coverages
    cov_by_req = {c.requirement_id: c for c in job_result.requirement_coverages}
    assert cov_by_req["REQ-001"].coverage_type == "covered_by_story"
    assert cov_by_req["REQ-002"].coverage_type == "covered_by_story"
    assert cov_by_req["REQ-003"].coverage_type == "needs_review"
    assert "clarification" in cov_by_req["REQ-003"].reason.lower() or "disposition" in cov_by_req["REQ-003"].reason.lower()

    # 4. Verify Export Rows
    assert len(job_result.exports.excel.rows) == 2
    assert len(job_result.exports.jira.rows) == 2


@pytest.mark.asyncio
async def test_multi_source_disposition_conflict_and_ghost_id_oracle(monkeypatch, mock_oracle_env):
    """Test cross-document disposition contradiction: Document A proposes/accepts feature, Document B rejects feature."""
    async def fake_llm_ainvoke(messages, **kwargs):
        system = messages[0][1] if isinstance(messages, list) and len(messages) > 0 else ""

        if "gatekeeper" in system.lower() or "relevance" in system.lower():
            return MagicMock(content=json.dumps({"is_useful": True, "relevance_score": 0.98, "reason": "valid spec"}))

        if "Extract atomic software requirements" in system or "Extract requirements" in system:
            return MagicMock(content=json.dumps({
                "requirements": [
                    {
                        "id": 1,
                        "text": "The platform shall support automated drone delivery for rural orders.",
                        "actor": "Logistics System",
                        "goal": "Support automated drone delivery",
                        "disposition": "accepted",
                        "candidate_labels": ["FR"],
                        "confidence": 0.95,
                        "priority": "High",
                        "evidence": [{"chunk_id": "c1", "quote": "The platform shall support automated drone delivery for rural orders."}]
                    },
                    {
                        "id": 2,
                        "text": "The platform shall support automated drone delivery for rural orders.",
                        "actor": "Logistics System",
                        "goal": "Automated drone delivery",
                        "disposition": "rejected",
                        "candidate_labels": ["Out-of-Scope"],
                        "confidence": 0.96,
                        "priority": "Low",
                        "evidence": [{"chunk_id": "c2", "quote": "Executive review concluded that drone delivery is rejected due to high operational liability."}]
                    },
                ]
            }))

        if "You classify each requirement" in system:
            return MagicMock(content=json.dumps({
                "classifications": [
                    {"id": 1, "labels": ["FR"], "confidence": 0.95},
                    {"id": 2, "labels": ["FR"], "confidence": 0.96},
                ]
            }))

        if "Convert requirements into USER STORIES" in system or "user stories" in system.lower():
            return MagicMock(content=json.dumps({
                "stories": [
                    {
                        "id": "logistics-sem-1_story_1",
                        "source_requirement_ids": [1],
                        "title": "Rural Drone Delivery Automation",
                        "description": "As a dispatcher, I want to assign rural orders to automated drones, so that delivery speed is optimized.",
                        "acceptance_criteria": [
                            "Given a rural order, when drone dispatch is triggered, then flight telemetry is activated.",
                            "Given active telemetry, when battery falls below 20%, then return-to-base is initiated."
                        ],
                        "labels": ["FR"],
                        "story_points": 5
                    }
                ]
            }))

        if "conflict" in system.lower():
            return MagicMock(content=json.dumps({"conflicts": []}))

        return MagicMock(content=json.dumps({
            "executive_summary": "Logistics operations plan.",
            "scope": ["Rural order logistics"],
            "out_of_scope": ["Drone delivery (rejected)"],
            "key_decisions": ["Review drone delivery liability"],
            "open_questions": []
        }))

    mock_llm_client = MagicMock()
    mock_llm_client.ainvoke = fake_llm_ainvoke

    from app import llm
    monkeypatch.setattr(llm, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extract, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(dedupe_requirements, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(classify, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(generate, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(summarize, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(repair_stories, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(ingest, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extractors, "get_llm", lambda: mock_llm_client)

    compiled_graph = build_pipeline()
    doc1_text = "The platform shall support automated drone delivery for rural orders."
    doc2_text = "Executive review concluded that drone delivery is rejected due to high operational liability."
    initial_state = {
        "job_id": "logistics-sem-1",
        "tenant_id": "ten-log",
        "project_id": "proj-log",
        "file_type": "text",
        "raw_text": f"{doc1_text}\n{doc2_text}",
        "language": "en",
        "raw_inputs": [
            {"document_id": "doc_prop", "filename": "proposal.txt", "file_type": "text", "raw_text": doc1_text},
            {"document_id": "doc_dec", "filename": "decision.txt", "file_type": "text", "raw_text": doc2_text},
        ],
        "source_documents": [
            {"document_id": "doc_prop", "filename": "proposal.txt", "file_type": "text"},
            {"document_id": "doc_dec", "filename": "decision.txt", "file_type": "text"},
        ],
        "chunks": [],
        "source_index_id": None,
        "retrieval_stats": None,
        "pii_stats": None,
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "quality_report": None,
        "job_result": None,
        "is_useful": True,
        "relevance_score": 0.98,
        "status": "started",
        "error": None,
        "started_at": 0.0,
        "processing_time_ms": 0,
        "repair_attempts": 0,
        "resolved_quality_issues": [],
        "functional_requirements": [],
        "processed_sources": None,
        "source_processing_stats": None,
        "partial_source_failure": False,
    }

    final_state = await compiled_graph.ainvoke(initial_state)
    job_result: JobResult = final_state.get("job_result")

    assert job_result is not None
    # Deduplication preserved both because one is accepted and one is rejected (conflicting dispositions)
    assert len(job_result.requirements) == 2

    # Invariant: Ghost ID verification across all warnings & issues
    valid_req_ids = {r.id for r in job_result.requirements}
    for warning in job_result.warnings:
        w_msg = warning.message if hasattr(warning, "message") else warning.get("message", "")
        for token in w_msg.split():
            clean_token = token.strip(".,;:()[]")
            if clean_token.startswith("REQ-"):
                assert clean_token in valid_req_ids, f"Ghost requirement ID '{clean_token}' in warning: '{w_msg}'"
