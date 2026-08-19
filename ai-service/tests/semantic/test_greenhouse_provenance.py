"""Greenhouse semantic integrity and provenance regression test.

Verifies:
1. Active requirements (including multi-utterance audio constraints) are extracted and grounded with verified evidence.
2. Rejected proposals (e.g. automatic fertilizer spraying) are assigned disposition='rejected', labeled 'Out-of-Scope', and gated out of story generation and Jira/Excel exports.
3. No ghost IDs in warnings or quality reports.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from app.graph.pipeline import build_pipeline
from app.schemas.items import SourceChunk, JobResult
from app.services.source_processing import audio
from app.nodes import ingest, extract, classify, generate, summarize


GREENHOUSE_TRANSCRIPT = (
    "Sarah (Agronomist): When the soil moisture in zone A falls below 24 percent, the Cedar irrigation valve must open automatically to protect the saplings.\n"
    "Elena (Lead Engineer): Understood. What about the midday sun between 12 PM and 2 PM?\n"
    "Sarah: Even if the soil gets dry, keep the Cedar valve closed between noon and 2 PM to avoid leaf scorching.\n"
    "Elena: Got it. Can the agronomist manually suspend automatic watering?\n"
    "Sarah: Yes, an agronomist should be able to suspend watering for up to 4 hours, and the system must display the suspension expiry time on screen.\n"
    "Elena: What about adding liquid fertilizer automatically whenever watering starts?\n"
    "Sarah: We discussed automatic fertilizer spraying last week, but we decided against it because it clogs the drip lines. Do not build that."
)


@pytest.fixture
def mock_greenhouse_pipeline(monkeypatch):
    monkeypatch.setattr(audio, "_validate_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "get_audio_duration_seconds", lambda *args, **kwargs: 35.0)

    class FakeEmbedder:
        model = "fake"
        async def embed_documents(self, texts):
            return [[0.1] * 1536 for _ in texts]
        async def embed_query(self, text):
            return [0.1] * 1536

    from app.rag import embeddings
    embeddings.set_embedder(FakeEmbedder())

    async def fake_transcribe(*args, **kwargs):
        utterances = [
            {"speaker": "Sarah", "start": 0.0, "end": 6.5, "text": "When the soil moisture in zone A falls below 24 percent, the Cedar irrigation valve must open automatically to protect the saplings.", "confidence": 0.98},
            {"speaker": "Elena", "start": 6.6, "end": 10.0, "text": "Understood. What about the midday sun between 12 PM and 2 PM?", "confidence": 0.97},
            {"speaker": "Sarah", "start": 10.1, "end": 15.2, "text": "Even if the soil gets dry, keep the Cedar valve closed between noon and 2 PM to avoid leaf scorching.", "confidence": 0.98},
            {"speaker": "Elena", "start": 15.3, "end": 18.0, "text": "Got it. Can the agronomist manually suspend automatic watering?", "confidence": 0.96},
            {"speaker": "Sarah", "start": 18.1, "end": 24.5, "text": "Yes, an agronomist should be able to suspend watering for up to 4 hours, and the system must display the suspension expiry time on screen.", "confidence": 0.98},
            {"speaker": "Elena", "start": 24.6, "end": 28.0, "text": "What about adding liquid fertilizer automatically whenever watering starts?", "confidence": 0.95},
            {"speaker": "Sarah", "start": 28.1, "end": 35.0, "text": "We discussed automatic fertilizer spraying last week, but we decided against it because it clogs the drip lines. Do not build that.", "confidence": 0.98},
        ]
        return GREENHOUSE_TRANSCRIPT, utterances

    monkeypatch.setattr("app.services.source_processing.audio._transcribe_groq", fake_transcribe)
    monkeypatch.setattr("app.nodes.transcribe._transcribe_groq", fake_transcribe)

    async def fake_llm_ainvoke(messages, **kwargs):
        system = messages[0][1] if isinstance(messages, list) and len(messages) > 0 else ""

        if "strict software-document gatekeeper" in system or "relevance" in system.lower() or "gatekeeper" in system.lower():
            return MagicMock(content=json.dumps({
                "is_useful": True,
                "relevance_score": 0.95,
                "reason": "valid greenhouse transcript"
            }))

        if "Extract atomic software requirements" in system or "Extract requirements" in system:
            return MagicMock(content=json.dumps({
                "requirements": [
                    {
                        "id": 1,
                        "text": "The Cedar irrigation valve must open automatically when soil moisture in zone A falls below 24 percent.",
                        "actor": "System",
                        "goal": "Open Cedar valve below 24 percent moisture",
                        "disposition": "accepted",
                        "candidate_labels": ["FR", "BR"],
                        "confidence": 0.98,
                        "priority": "High",
                        "evidence": [{"chunk_id": "c1", "quote": "soil moisture in zone A falls below 24 percent, the Cedar irrigation valve must open automatically"}]
                    },
                    {
                        "id": 2,
                        "text": "The Cedar irrigation valve must remain closed between 12 PM and 2 PM even if the soil is dry.",
                        "actor": "System",
                        "goal": "Keep Cedar valve closed noon to 2 PM",
                        "disposition": "accepted",
                        "candidate_labels": ["BR"],
                        "confidence": 0.95,
                        "priority": "High",
                        # Multi-utterance quote span across dialogue turns
                        "evidence": [{"chunk_id": "c1", "quote": "between 12 PM and 2 PM? Sarah: Even if the soil gets dry, keep the Cedar valve closed between noon and 2 PM"}]
                    },
                    {
                        "id": 3,
                        "text": "An agronomist shall be able to suspend automatic watering for up to 4 hours and view the suspension expiry time.",
                        "actor": "Agronomist",
                        "goal": "Suspend watering up to 4 hours with expiry display",
                        "disposition": "accepted",
                        "candidate_labels": ["FR"],
                        "confidence": 0.96,
                        "priority": "Medium",
                        "evidence": [{"chunk_id": "c1", "quote": "agronomist should be able to suspend watering for up to 4 hours, and the system must display the suspension expiry time on screen."}]
                    },
                    {
                        "id": 4,
                        "text": "The system should automatically spray liquid fertilizer whenever watering starts.",
                        "actor": "System",
                        "goal": "Spray fertilizer during watering",
                        "disposition": "rejected",
                        "candidate_labels": ["Out-of-Scope"],
                        "confidence": 0.95,
                        "priority": "Low",
                        "evidence": [{"chunk_id": "c1", "quote": "automatic fertilizer spraying last week, but we decided against it because it clogs the drip lines."}]
                    }
                ]
            }))

        if "You classify each requirement" in system:
            return MagicMock(content=json.dumps({
                "classifications": [
                    {"id": 1, "labels": ["FR", "BR"], "confidence": 0.98},
                    {"id": 2, "labels": ["BR"], "confidence": 0.95},
                    {"id": 3, "labels": ["FR"], "confidence": 0.96},
                    {"id": 4, "labels": ["FR"], "confidence": 0.95},
                ]
            }))

        if "Convert requirements into USER STORIES" in system or "user stories" in system.lower():
            # LLM only called for actionable requirements (1, 2, 3)
            return MagicMock(content=json.dumps({
                "stories": [
                    {
                        "source_requirement_ids": [1],
                        "title": "Automated Moisture-Based Irrigation",
                        "description": "As a system, I want to open the Cedar irrigation valve when soil moisture in zone A drops below 24%, so that saplings are protected.",
                        "acceptance_criteria": [
                            "Given soil moisture in zone A is below 24%, when the sensor reading updates, then the Cedar valve opens automatically.",
                            "Given soil moisture in zone A is at or above 24%, when the reading updates, then the Cedar valve remains in its current state."
                        ],
                        "labels": ["FR", "BR"],
                        "story_points": 3
                    },
                    {
                        "source_requirement_ids": [2],
                        "title": "Midday Sun Irrigation Lockout",
                        "description": "As a system, I want to keep the Cedar valve closed between 12 PM and 2 PM, so that leaf scorching is prevented.",
                        "acceptance_criteria": [
                            "Given the current time is between 12:00 PM and 2:00 PM, when soil moisture is dry, then the Cedar valve remains closed.",
                            "Given the current time reaches 2:01 PM, when soil moisture is below 24%, then automated watering may resume."
                        ],
                        "labels": ["BR"],
                        "story_points": 2
                    },
                    {
                        "source_requirement_ids": [3],
                        "title": "Agronomist Manual Irrigation Suspension",
                        "description": "As an agronomist, I want to suspend automatic watering for up to 4 hours and see the expiry time, so that field inspections proceed without watering.",
                        "acceptance_criteria": [
                            "Given an authenticated agronomist, when a suspension of up to 4 hours is requested, then automatic watering is paused.",
                            "Given an active suspension, when viewing the screen, then the exact expiry time of the suspension is displayed."
                        ],
                        "labels": ["FR"],
                        "story_points": 3
                    }
                ]
            }))

        if "conflict" in system.lower() or "detect whether any pairs" in system.lower():
            return MagicMock(content=json.dumps({"conflicts": []}))

        return MagicMock(content=json.dumps({
            "executive_summary": "Irrigation automation policy for sapling protection.",
            "scope": ["Moisture threshold valve automation", "Midday lockout", "Manual suspension"],
            "out_of_scope": ["Automatic liquid fertilizer spraying (rejected)"],
            "key_decisions": ["Keep Cedar valve closed 12 PM to 2 PM to avoid leaf scorching"],
            "open_questions": []
        }))

    mock_llm_client = MagicMock()
    mock_llm_client.ainvoke = fake_llm_ainvoke

    from app import llm
    from app.nodes import dedupe_requirements, repair_stories
    from app.services.source_processing import extractors
    monkeypatch.setattr(llm, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extract, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(dedupe_requirements, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(classify, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(generate, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(summarize, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(repair_stories, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(ingest, "get_llm", lambda: mock_llm_client)
    monkeypatch.setattr(extractors, "get_llm", lambda: mock_llm_client)


@pytest.mark.asyncio
async def test_greenhouse_provenance_and_rejection_gating(mock_greenhouse_pipeline):
    """End-to-end greenhouse pipeline execution validating semantic integrity."""
    compiled_graph = build_pipeline()

    initial_state = {
        "job_id": "gh-sem-1",
        "tenant_id": "ten-gh",
        "project_id": "proj-gh",
        "file_type": "audio",
        "language": "en",
        "raw_inputs": [
            {
                "document_id": "audio_gh_meeting",
                "filename": "greenhouse_meeting.mp3",
                "file_type": "audio",
                "mime_type": "audio/mpeg",
                "audio_format": "mp3",
                "raw_bytes": b"ID3\x03fake-greenhouse-audio",
            }
        ],
        "source_documents": [
            {
                "document_id": "audio_gh_meeting",
                "filename": "greenhouse_meeting.mp3",
                "file_type": "audio",
                "mime_type": "audio/mpeg",
            }
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
        "relevance_score": 0.95,
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
    assert job_result.status == "completed"

    # 1. Requirements verification
    assert len(job_result.requirements) == 4
    req_by_id = {r.id: r for r in job_result.requirements}

    # Verify active requirements have verified evidence citations
    assert len(req_by_id["REQ-001"].source_refs) >= 1
    assert "24" in req_by_id["REQ-001"].description
    assert len(req_by_id["REQ-002"].source_refs) >= 1
    assert "12 PM" in req_by_id["REQ-002"].description or "noon" in req_by_id["REQ-002"].description.lower()
    assert len(req_by_id["REQ-003"].source_refs) >= 1
    assert "4 hours" in req_by_id["REQ-003"].description

    # 2. Rejected Proposal Leakage Gate (Requirement 4)
    req_4 = req_by_id["REQ-004"]
    assert "fertilizer" in req_4.description.lower()
    assert req_4.category == "Out-of-Scope"

    # 3. User Story Gating: Stories must ONLY exist for actionable requirements 1, 2, 3
    assert len(job_result.user_stories) == 3
    for story in job_result.user_stories:
        assert "fertilizer" not in story.title.lower()
        assert "fertilizer" not in story.user_story.lower()
        assert story.requirement_id in ("REQ-001", "REQ-002", "REQ-003")
        assert req_4.id != story.requirement_id

    # 4. Coverage Verification
    coverage_by_req = {c.requirement_id: c for c in job_result.requirement_coverages}
    assert coverage_by_req["REQ-001"].coverage_type == "covered_by_story"
    assert coverage_by_req["REQ-002"].coverage_type == "covered_by_story"
    assert coverage_by_req["REQ-003"].coverage_type == "covered_by_story"
    assert coverage_by_req["REQ-004"].coverage_type == "non_story"

    # 5. Export Rows Gating (Excel and Jira)
    assert job_result.exports.excel.available is True
    assert len(job_result.exports.excel.rows) == 3
    for row in job_result.exports.excel.rows:
        assert "fertilizer" not in str(row).lower()
        assert row["requirement_id"] in ("REQ-001", "REQ-002", "REQ-003")

    assert job_result.exports.jira.available is True
    assert len(job_result.exports.jira.rows) == 3
    for row in job_result.exports.jira.rows:
        assert "fertilizer" not in str(row).lower()

    # 6. Ghost ID & Warning Integrity Check
    valid_req_ids = {r.id for r in job_result.requirements}
    for warning in job_result.warnings:
        w_msg = warning.message if hasattr(warning, "message") else warning.get("message", "")
        # No reference to ghost REQ IDs outside valid_req_ids
        for token in w_msg.split():
            clean_token = token.strip(".,;:()[]")
            if clean_token.startswith("REQ-"):
                assert clean_token in valid_req_ids, f"Ghost requirement ID '{clean_token}' in warning: '{w_msg}'"
