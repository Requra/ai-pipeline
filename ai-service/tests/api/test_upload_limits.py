import io
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import settings


@pytest.fixture(autouse=True)
def _mock_background_dispatch(monkeypatch):
    from app.worker import dispatch
    async def fake_dispatch(*args, **kwargs):
        return True

    monkeypatch.setattr(dispatch, "dispatch_job", fake_dispatch)
    from app.api import service
    monkeypatch.setattr(service, "dispatch_job", fake_dispatch)


@pytest.mark.asyncio
async def test_upload_valid_files_within_limits():
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        files = [
            ("files", ("spec.pdf", b"%PDF-1.4\ncontent", "application/pdf")),
            ("files", ("notes.txt", b"Plain text requirements", "text/plain")),
        ]
        data = {
            "job_id": "test-limits-valid-1",
            "project_id": "proj-limits",
            "document_ids": ["doc_pdf", "doc_txt"],
        }
        resp = await client.post("/internal/process", headers=headers, data=data, files=files)
        assert resp.status_code == 202


@pytest.mark.asyncio
async def test_upload_empty_file_rejected():
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        files = [
            ("files", ("empty.txt", b"", "text/plain")),
        ]
        data = {
            "job_id": "test-limits-empty-1",
            "project_id": "proj-limits",
        }
        resp = await client.post("/internal/process", headers=headers, data=data, files=files)
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_individual_file_oversized_rejected():
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Mock max doc size to a smaller threshold for test
        original_limit = settings.MAX_DOCUMENT_BYTES
        try:
            settings.MAX_DOCUMENT_BYTES = 1024  # 1 KB
            oversized_pdf = b"%PDF-1.4\n" + b"A" * 2048
            files = [
                ("files", ("oversized.pdf", oversized_pdf, "application/pdf")),
            ]
            data = {
                "job_id": "test-limits-oversized-1",
                "project_id": "proj-limits",
            }
            resp = await client.post("/internal/process", headers=headers, data=data, files=files)
            assert resp.status_code == 413
            assert "too large" in resp.json()["detail"]
        finally:
            settings.MAX_DOCUMENT_BYTES = original_limit


@pytest.mark.asyncio
async def test_upload_aggregate_size_oversized_rejected():
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        original_agg = settings.MAX_TOTAL_UPLOAD_BYTES
        original_doc = settings.MAX_DOCUMENT_BYTES
        try:
            settings.MAX_TOTAL_UPLOAD_BYTES = 2000  # 2 KB aggregate
            settings.MAX_DOCUMENT_BYTES = 1500      # 1.5 KB per doc
            doc1 = b"%PDF-1.4\n" + b"A" * 1200
            doc2 = b"%PDF-1.4\n" + b"B" * 1200      # total = 2400 > 2000
            files = [
                ("files", ("doc1.pdf", doc1, "application/pdf")),
                ("files", ("doc2.pdf", doc2, "application/pdf")),
            ]
            data = {
                "job_id": "test-limits-agg-1",
                "project_id": "proj-limits",
                "document_ids": ["d1", "d2"],
            }
            resp = await client.post("/internal/process", headers=headers, data=data, files=files)
            assert resp.status_code == 413
            assert "Aggregate upload size" in resp.json()["detail"]
        finally:
            settings.MAX_TOTAL_UPLOAD_BYTES = original_agg
            settings.MAX_DOCUMENT_BYTES = original_doc


@pytest.mark.asyncio
async def test_upload_too_many_sources_rejected():
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.AI_INTERNAL_SERVICE_TOKEN}"}
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        original_max_sources = settings.MAX_SOURCES_PER_JOB
        try:
            settings.MAX_SOURCES_PER_JOB = 2
            files = [
                ("files", ("doc1.txt", b"one", "text/plain")),
                ("files", ("doc2.txt", b"two", "text/plain")),
                ("files", ("doc3.txt", b"three", "text/plain")),
            ]
            data = {
                "job_id": "test-limits-count-1",
                "project_id": "proj-limits",
            }
            resp = await client.post("/internal/process", headers=headers, data=data, files=files)
            assert resp.status_code == 400
            assert "Too many uploaded files" in resp.json()["detail"]
        finally:
            settings.MAX_SOURCES_PER_JOB = original_max_sources
