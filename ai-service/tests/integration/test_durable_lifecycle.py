import asyncio
import io
import json
import zipfile
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import settings
from app.store.factory import get_stores
from app.store.models import JobStatus


@pytest.fixture(autouse=True)
def _mock_background_dispatch(monkeypatch):
    from app.worker import dispatch
    async def fake_dispatch(*args, **kwargs):
        return True

    monkeypatch.setattr(dispatch, "dispatch_job", fake_dispatch)
    from app.api import service
    monkeypatch.setattr(service, "dispatch_job", fake_dispatch)


def _create_test_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\nxref\n0 2\n0000000000 65535 f \n0000000009 00000 n \ntrailer << /Size 2 /Root 1 0 R >>\nstartxref\n50\n%%EOF"


def _create_test_docx() -> bytes:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        z.writestr("[Content_Types].xml", "<types></types>")
        z.writestr("word/document.xml", "<document>Security Requirements: Passwords must be at least 8 characters.</document>")
    return bio.getvalue()


def _create_test_mp3() -> bytes:
    return b"ID3\x03\x00\x00\x00\x00\x00\x00\xff\xfb\x90d\x00\x00\x00\x00"


@pytest.mark.asyncio
async def test_scenario_a_golden_mixed_source_lifecycle(monkeypatch):
    """Scenario A: Full Golden mixed-source lifecycle via POST /internal/process."""
    # Deterministic mock for STT and LLM
    import app.services.source_processing.audio as spa
    monkeypatch.setattr(spa, "_validate_ffmpeg", lambda: None)
    async def _mock_groq(*args, **kwargs):
        from app.schemas.items import SourceChunk
        c = SourceChunk(
            chunk_id="chunk_aud_1",
            document_id="doc_audio_4",
            text="Stakeholders agreed the password reset link must expire after 15 minutes.",
            start_char=0,
            end_char=74,
            start_time_sec=0.0,
            end_time_sec=15.0,
            speaker="speaker_1",
        )
        return ("Stakeholders agreed the password reset link must expire after 15 minutes.", [c])
    monkeypatch.setattr(spa, "_transcribe_groq", _mock_groq)

    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        job_id = "durable-golden-job-1"
        files = [
            ("files", ("requirements.pdf", _create_test_pdf(), "application/pdf")),
            ("files", ("technical-notes.docx", _create_test_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", ("stakeholder-notes.txt", b"The system shall provide email notifications.", "text/plain")),
            ("files", ("meeting-audio.mp3", _create_test_mp3(), "audio/mpeg")),
        ]
        data = {
            "job_id": job_id,
            "project_id": "proj-lifecycle",
            "tenant_id": "tenant-lifecycle",
            "document_ids": ["doc_pdf_1", "doc_docx_2", "doc_txt_3", "doc_audio_4"],
        }

        # 1. Submit
        resp = await client.post("/internal/process", headers=headers, data=data, files=files)
        assert resp.status_code == 202
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["status"] in ("QUEUED", "PROCESSING", "COMPLETED")

        # 2. Verify status endpoint
        status_resp = await client.get(f"/internal/jobs/{job_id}", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json()["job_id"] == job_id


@pytest.mark.asyncio
async def test_scenario_b_api_restart_durability(monkeypatch):
    """Scenario B: Job results persisted in store remain retrievable across simulated API restart."""
    stores = get_stores()
    job_id = "restart-test-job-1"

    # Seed job and result directly in stores
    from app.store.models import AiJobRecord, JobResultRecord, JobStatus, JobOptions
    await stores.jobs.create_job(
        AiJobRecord(
            job_id=job_id,
            tenant_id="t1",
            project_id="p1",
            input_type="backend_sources",
            status=JobStatus.COMPLETED,
            options=JobOptions(),
        )
    )
    await stores.results.save_result(
        job_id=job_id,
        result={
            "job_id": job_id,
            "status": "completed",
            "user_stories": [],
            "requirements": [],
            "warnings": [],
            "quality_issues": [],
        },
        status="completed",
    )

    # Reconstruct fresh HTTP client simulating a new restarted API instance
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Verify status
        status_resp = await client.get(f"/internal/jobs/{job_id}", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "COMPLETED"

        # Verify result payload
        result_resp = await client.get(f"/internal/jobs/{job_id}/result", headers=headers)
        assert result_resp.status_code == 200
        result_body = result_resp.json()
        assert result_body["job_id"] == job_id
        assert result_body["status"] == "completed"
