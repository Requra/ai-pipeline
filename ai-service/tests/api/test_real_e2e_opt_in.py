"""
Opt-in Real Provider End-to-End Test Suite.
Requires real external AI provider credentials and is skipped during standard CI unless
specifically targeted with:
    pytest -m real_e2e
"""
import os
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import settings

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "test-fixtures" / "e2e_real_mixed"


@pytest.mark.real_e2e
@pytest.mark.asyncio
async def test_real_mixed_source_e2e_pipeline():
    """Verify complete mixed audio + document processing with real Groq LLM & STT."""
    if not settings.GROQ_API_KEY:
        pytest.skip("GROQ_API_KEY not configured for real E2E testing.")

    pdf_path = FIXTURES_DIR / "requirements.pdf"
    docx_path = FIXTURES_DIR / "technical-notes.docx"
    txt_path = FIXTURES_DIR / "stakeholder-notes.txt"
    audio_path = FIXTURES_DIR / "meeting-audio.mp3"

    if not all(p.exists() for p in [pdf_path, docx_path, txt_path, audio_path]):
        pytest.skip("E2E real mixed fixtures missing.")

    transport = ASGITransport(app=app)
    auth_headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN or 'e2e-token'}"}

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        files = [
            ("files", ("requirements.pdf", open(pdf_path, "rb"), "application/pdf")),
            ("files", ("technical-notes.docx", open(docx_path, "rb"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", ("stakeholder-notes.txt", open(txt_path, "rb"), "text/plain")),
            ("files", ("meeting-audio.mp3", open(audio_path, "rb"), "audio/mpeg")),
        ]
        data = {
            "job_id": f"e2e-pytest-optin-{os.getpid()}",
            "project_id": "proj-e2e",
            "tenant_id": "tenant-e2e",
            "document_ids": ["doc_pdf", "doc_docx", "doc_txt", "doc_audio"],
            "language": "en",
        }

        resp = await client.post("/internal/process", headers=auth_headers, data=data, files=files)
        assert resp.status_code in (200, 202)
        resp_data = resp.json()
        assert resp_data.get("job_id") == data["job_id"]
        assert resp_data.get("input_type") == "backend_sources"
