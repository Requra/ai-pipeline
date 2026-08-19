import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import settings
from app.nodes.detect_file_type import detect_file_type_node


@pytest.mark.asyncio
async def test_detect_file_type_rejects_mixed_sources_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_MIXED_SOURCE_JOBS", False)

    state = {
        "job_id": "test_mixed_flag_1",
        "raw_inputs": [
            {"document_id": "d1", "filename": "spec.pdf", "raw_bytes": b"%PDF-1.4\ncontent"},
            {"document_id": "d2", "filename": "meeting.mp3", "raw_bytes": b"ID3\x03fake-audio"},
        ],
    }

    result = await detect_file_type_node(state)
    assert result["status"] == "rejected"
    assert "ENABLE_MIXED_SOURCE_JOBS" in result["error"]


@pytest.mark.asyncio
async def test_api_rejects_mixed_sources_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_MIXED_SOURCE_JOBS", False)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        files = [
            ("files", ("spec.pdf", b"%PDF-1.4\ncontent", "application/pdf")),
            ("files", ("meeting.mp3", b"ID3\x03fake-audio", "audio/mpeg")),
        ]
        data = {
            "job_id": "test-flag-disabled-1",
            "project_id": "p1",
            "document_ids": ["d1", "d2"],
        }
        resp = await client.post("/internal/process", headers=headers, data=data, files=files)
        assert resp.status_code == 400
        assert "ENABLE_MIXED_SOURCE_JOBS" in resp.json()["detail"]
