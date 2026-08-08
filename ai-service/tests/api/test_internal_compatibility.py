"""
Regression tests for compatibility upload/processing API endpoints and safety downloader.
"""

from __future__ import annotations

import io
import json
import zipfile
import asyncio
from pathlib import Path
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
MULTIPART_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "test-fixtures" / "multipart_upload"


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

    initial_state = mocked_pipeline.ainvoke.await_args.args[0]
    assert initial_state["raw_bytes"] == mp3_bytes
    assert initial_state["raw_inputs"] == []
    assert initial_state["file_type"] == "audio"
    assert initial_state["audio_format"] == "mp3"


def test_process_single_audio_in_files_uses_single_source_transcription_path(
    client, mocked_pipeline
):
    mp3_bytes = b"ID3\x03\x00\x00\x00\x00\x00\x00"
    data, files = _multi_upload(
        [("meeting.mp3", mp3_bytes, "audio/mpeg")],
        job_id="compat-files-single-audio",
        document_ids=["audio-source"],
    )

    response = client.post("/internal/process", headers=AUTH, data=data, files=files)

    assert response.status_code == 202
    initial_state = mocked_pipeline.ainvoke.await_args.args[0]
    assert initial_state["raw_bytes"] == mp3_bytes
    assert initial_state["raw_inputs"] == []
    assert initial_state["file_type"] == "audio"
    assert initial_state["audio_format"] == "mp3"


def _multi_upload(files, *, job_id="compat-multi", document_ids=None):
    multipart_files = [
        ("job_id", (None, job_id)),
        ("project_id", (None, "proj-multi")),
        ("tenant_id", (None, "ten-multi")),
    ]
    for document_id in document_ids or []:
        multipart_files.append(("document_ids", (None, document_id)))
    multipart_files.extend(("files", item) for item in files)
    return {}, multipart_files


def test_process_multipart_accepts_multiple_documents_and_persists_manifest(client, mocked_pipeline):
    data, files = _multi_upload(
        [
            ("requirements.txt", b"The system must let users reset their passwords securely.", "text/plain"),
            ("api.txt", b"The API must return a status code and an actionable error message.", "text/plain"),
        ],
        document_ids=["doc-requirements", "doc-api"],
    )

    response = client.post("/internal/process", headers=AUTH, data=data, files=files)
    assert response.status_code == 202

    docs = _run(get_stores().chunks.get_documents("compat-multi"))
    assert [(doc.backend_document_id, doc.file_name) for doc in docs] == [
        ("doc-requirements", "requirements.txt"),
        ("doc-api", "api.txt"),
    ]


def test_process_multiple_documents_end_to_end_preserves_provenance(client, monkeypatch):
    """Exercise endpoint -> dispatch -> worker -> real ingest/chunk nodes -> durable result."""
    from app.nodes import ingest
    from app.nodes.detect_file_type import detect_file_type_node
    from app.nodes.parse_to_chunks import parse_to_chunks_node
    from app.schemas.items import JobResult, SourceDocumentV1

    async def useful_relevance(_: str) -> ingest.RelevanceCheck:
        return ingest.RelevanceCheck(is_useful=True, relevance_score=0.96, reason="requirements")

    class ProductionShapedPipeline:
        async def ainvoke(self, initial_state):
            state = {**initial_state, **await detect_file_type_node(initial_state)}
            state = {**state, **await ingest.ingest_node(state)}
            state = {**state, **await parse_to_chunks_node(state)}
            sources = [
                SourceDocumentV1(
                    source_id=doc["document_id"],
                    source_type=doc["file_type"],
                    file_name=doc["filename"],
                    mime_type=doc["mime_type"],
                )
                for doc in state["source_documents"]
            ]
            result = JobResult(
                job_id=state["job_id"],
                status="completed",
                is_useful=True,
                relevance_score=state["relevance_score"],
                source_documents=sources,
                processing_time_ms=1,
            )
            return {**state, "status": "completed", "job_result": result}

    monkeypatch.setattr(ingest, "_run_relevance_check", useful_relevance)
    monkeypatch.setattr("app.main.pipeline", ProductionShapedPipeline())
    docx_path = MULTIPART_FIXTURE_ROOT / "customer_workspace_requirements.docx"
    pdf_path = MULTIPART_FIXTURE_ROOT / "operations_case_management.pdf"
    assert docx_path.exists() and pdf_path.exists()
    data, files = _multi_upload(
        [
            (docx_path.name, docx_path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            (pdf_path.name, pdf_path.read_bytes(), "application/pdf"),
        ],
        job_id="multi-e2e-1",
        document_ids=["workspace-source", "operations-source"],
    )

    submitted = client.post("/internal/process", headers=AUTH, data=data, files=files)
    assert submitted.status_code == 202
    assert submitted.json()["idempotent"] is False

    status = client.get("/internal/jobs/multi-e2e-1", headers=AUTH)
    assert status.status_code == 200
    assert status.json()["status"] == "COMPLETED"

    result = client.get("/internal/jobs/multi-e2e-1/result", headers=AUTH)
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert [(doc["source_id"], doc["source_type"], doc["file_name"]) for doc in result.json()["source_documents"]] == [
        ("workspace-source", "docx", docx_path.name),
        ("operations-source", "pdf", pdf_path.name),
    ]

    docs = _run(get_stores().chunks.get_documents("multi-e2e-1"))
    chunks = _run(get_stores().chunks.get_chunks("multi-e2e-1"))
    assert {chunk.source_document_id for chunk in chunks} == {doc.id for doc in docs}
    assert len(chunks) >= 5
    assert any("Community garden meeting minutes" in chunk.text for chunk in chunks)
    assert any("Weekend travel notes" in chunk.text for chunk in chunks)


def test_duplicate_multi_upload_is_idempotent_and_changed_second_file_conflicts(client, mocked_pipeline):
    first_data, first_files = _multi_upload(
        [
            ("one.txt", b"The system must support account creation and email confirmation.", "text/plain"),
            ("two.txt", b"The system must expose an audit log for administrator actions.", "text/plain"),
        ],
        job_id="compat-multi-idem",
    )
    assert client.post("/internal/process", headers=AUTH, data=first_data, files=first_files).status_code == 202

    duplicate_data, duplicate_files = _multi_upload(
        [
            ("one.txt", b"The system must support account creation and email confirmation.", "text/plain"),
            ("two.txt", b"The system must expose an audit log for administrator actions.", "text/plain"),
        ],
        job_id="compat-multi-idem",
    )
    duplicate = client.post("/internal/process", headers=AUTH, data=duplicate_data, files=duplicate_files)
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True

    changed_data, changed_files = _multi_upload(
        [
            ("one.txt", b"The system must support account creation and email confirmation.", "text/plain"),
            ("two.txt", b"The system must require MFA for administrator actions.", "text/plain"),
        ],
        job_id="compat-multi-idem",
    )
    changed = client.post("/internal/process", headers=AUTH, data=changed_data, files=changed_files)
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "JOB_ID_CONFLICT"


@pytest.mark.parametrize(
    ("files", "expected_status", "message"),
    [
        (
            [
                ("valid.txt", b"The system must have enough text to be a valid upload.", "text/plain"),
                ("empty.txt", b"", "text/plain"),
            ],
            400,
            "empty",
        ),
        (
            [
                ("valid.txt", b"The system must have enough text to be a valid upload.", "text/plain"),
                ("unknown.bin", b"\xc3\x28\x00\x00", "application/octet-stream"),
            ],
            415,
            "unsupported",
        ),
        (
            [
                ("valid.txt", b"The system must have enough text to be a valid upload.", "text/plain"),
                ("audio.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00", "audio/mpeg"),
            ],
            400,
            "mixed document and audio",
        ),
    ],
)
def test_process_multi_upload_validation(client, files, expected_status, message):
    data, multipart_files = _multi_upload(files, job_id=f"validation-{expected_status}-{message[:4]}")
    response = client.post("/internal/process", headers=AUTH, data=data, files=multipart_files)
    assert response.status_code == expected_status
    assert message in response.json()["detail"].lower()


def test_process_multi_upload_rejects_oversized_document(client):
    data, files = _multi_upload(
        [
            ("valid.txt", b"The system must have enough text to be a valid upload.", "text/plain"),
            ("large.txt", b"x" * (20 * 1024 * 1024 + 1), "text/plain"),
        ],
        job_id="validation-oversized",
    )
    response = client.post("/internal/process", headers=AUTH, data=data, files=files)
    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_worker_reconstructs_multiple_cached_uploads(monkeypatch):
    from app.worker.state import build_worker_initial_state, stash_input

    class FakeRedis:
        values = {}

        def set(self, key, value, ex):
            self.values[key] = value
            assert ex > 0
            return True

        def get(self, key):
            return self.values.get(key)

    fake_redis = FakeRedis()
    monkeypatch.setattr("app.queue.redis_queue.get_redis_connection", lambda: fake_redis)
    stash_input(
        "worker-multi",
        file_type="document",
        raw_inputs=[
            {
                "document_id": "doc-a", "filename": "a.txt", "file_type": "text",
                "mime_type": "text/plain", "raw_bytes": b"alpha",
            },
            {
                "document_id": "doc-b", "filename": "b.txt", "file_type": "text",
                "mime_type": "text/plain", "raw_bytes": b"beta",
            },
        ],
        source_documents=[{"document_id": "doc-a"}, {"document_id": "doc-b"}],
    )
    job = AiJobRecord(
        job_id="worker-multi",
        input_type="backend_document",
    )
    state = await build_worker_initial_state(job, get_stores())
    assert [(item["document_id"], item["raw_bytes"]) for item in state["raw_inputs"]] == [
        ("doc-a", b"alpha"),
        ("doc-b", b"beta"),
    ]


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
