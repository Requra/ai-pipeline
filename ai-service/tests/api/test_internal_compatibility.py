"""
Regression tests for compatibility upload/processing API endpoints and safety downloader.
"""

from __future__ import annotations

import io
import json
import zipfile
import asyncio
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.progress import progress_store
from app.store.factory import get_stores, reset_stores
from app.store.models import AiJobRecord, JobStatus, SourceDocumentRecord
from app.clients.backend import (
    BackendDocumentClient,
    SourceSecurityError,
    SourceUnavailableError,
)

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(settings, "AI_INTERNAL_SERVICE_TOKEN", TOKEN)
    reset_stores()
    progress_store.clear()
    yield
    reset_stores()
    progress_store.clear()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def mocked_pipeline():
    from unittest.mock import AsyncMock, MagicMock, patch

    mock = MagicMock()
    mock.ainvoke = AsyncMock(
        return_value={
            "status": "completed",
            "job_result": {
                "job_id": "compat-job",
                "status": "completed",
                "requirements": [],
                "user_stories": [],
                "quality_report": {"overall_score": 1.0},
            },
        }
    )
    with patch("app.main.pipeline", mock):
        yield mock


def test_process_json_compatibility(client, mocked_pipeline):
    payload = {
        "job_id": "compat-json-1",
        "project_id": "proj-1",
        "tenant_id": "ten-1",
        "source_type": "meeting_transcript",
        "content": "Meeting context: user requests a reset password flow.",
        "options": {"generate_user_stories": True, "language": "en"},
    }
    resp = client.post("/internal/process-json", headers=AUTH, json=payload)
    assert resp.status_code == 202
    assert resp.json()["status"] == "QUEUED"

    # Check that job record exists
    stores = get_stores()
    job = _run(stores.jobs.get_job("compat-json-1"))
    assert job is not None
    assert job.tenant_id == "ten-1"
    assert job.project_id == "proj-1"


def test_process_multipart_compatibility_docx(client, mocked_pipeline):
    # Construct a minimal docx file
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        z.writestr("[Content_Types].xml", "<types></types>")
        z.writestr("word/document.xml", "<document></document>")
    docx_bytes = bio.getvalue()

    files = {
        "file": (
            "test.docx",
            docx_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    }
    data = {
        "job_id": "compat-multipart-docx",
        "project_id": "proj-docx",
        "tenant_id": "ten-docx",
        "metadata": json.dumps({"tenant_id": "ten-docx"}),
        "reprocess": "true",
    }

    resp = client.post("/internal/process", headers=AUTH, files=files, data=data)
    assert resp.status_code == 202
    assert resp.json()["status"] == "QUEUED"

    # Check database source document record
    stores = get_stores()
    docs = _run(stores.chunks.get_documents("compat-multipart-docx"))
    assert len(docs) == 1
    assert docs[0].source_type == "docx"
    assert (
        docs[0].mime_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_process_multipart_compatibility_audio(client, mocked_pipeline):
    # Minimal MP3 signature: ID3
    mp3_bytes = b"ID3\x03\x00\x00\x00\x00\x00\x00"

    files = {"file": ("test.mp3", mp3_bytes, "audio/mpeg")}
    data = {
        "job_id": "compat-multipart-audio",
        "project_id": "proj-audio",
        "metadata": json.dumps({"description": "test audio"}),
    }

    resp = client.post("/internal/process", headers=AUTH, files=files, data=data)
    assert resp.status_code == 202
    assert resp.json()["status"] == "QUEUED"

    stores = get_stores()
    docs = _run(stores.chunks.get_documents("compat-multipart-audio"))
    assert len(docs) == 1
    assert docs[0].source_type == "audio"
    assert docs[0].mime_type == "audio/mpeg"


def test_content_recovery_endpoint(client, mocked_pipeline):
    # Upload first
    mp3_bytes = b"ID3\x03\x00\x00\x00\x00\x00\x00"
    files = {"file": ("test.mp3", mp3_bytes, "audio/mpeg")}
    data = {
        "job_id": "recovery-job",
        "project_id": "proj-rec",
        "document_id": "doc-custom-id",
    }

    client.post("/internal/process", headers=AUTH, files=files, data=data)

    # Now retrieve content
    resp = client.get("/internal/documents/doc-custom-id/content", headers=AUTH)
    assert resp.status_code == 200
    assert resp.content == mp3_bytes
    assert resp.headers["content-type"] == "audio/mpeg"


# ── SSRF Security & Two-Tier Host Downloader Tests ───────────────────────────────


@pytest.mark.asyncio
async def test_downloader_rejects_unsafe_hosts():
    from app.clients.backend import _validate_host_safety

    # 127.0.0.1 is loopback and unsafe for external storage
    with pytest.raises(SourceSecurityError):
        _validate_host_safety("127.0.0.1", is_backend=False)

    # 10.0.0.1 is private and unsafe
    with pytest.raises(SourceSecurityError):
        _validate_host_safety("10.0.0.1", is_backend=False)

    # Configured backend IS allowed to resolve to private host
    _validate_host_safety("127.0.0.1", is_backend=True)


@pytest.mark.asyncio
async def test_downloader_rejects_non_approved_domain():
    client = BackendDocumentClient(base_url="https://api.requra.ai")

    # Host not allowlisted
    ref = {"document_id": "D-1", "file_url": "https://malicious-domain.com/evil.pdf"}
    with pytest.raises(SourceSecurityError):
        await client.fetch_document_bytes(ref)


@pytest.mark.asyncio
async def test_downloader_rejects_userinfo_urls():
    client = BackendDocumentClient(base_url="https://api.requra.ai")
    ref = {
        "document_id": "D-1",
        "file_url": "https://user:pass@s3.amazonaws.com/requra/spec.pdf",
    }
    with pytest.raises(SourceSecurityError):
        await client.fetch_document_bytes(ref)


@pytest.mark.asyncio
async def test_callback_rejects_non_backend_origin_without_sending_token(monkeypatch):
    called = False

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            nonlocal called
            called = True
            return self

        async def __aexit__(self, *args):
            return None

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", DummyClient)
    client = BackendDocumentClient(
        base_url="https://backend.requra.internal",
        service_token="backend-secret",
    )

    ok = await client.send_callback(
        "https://evil.example/callback?token=abc",
        {"job_id": "cb-reject"},
    )

    assert ok is False
    assert called is False


@pytest.mark.asyncio
async def test_dispatch_fails_when_redis_input_cache_fails(monkeypatch):
    from app.worker.dispatch import dispatch_job
    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", "redis://example.invalid:6379/0")

    def fail_stash(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.worker.state.stash_input", fail_stash)

    with pytest.raises(RuntimeError, match="INPUT_CACHE_FAILED"):
        await dispatch_job(
            "cache-failure-job",
            initial_state={"job_id": "cache-failure-job", "raw_text": "hello"},
            cache_input={"raw_text": "hello"},
        )
