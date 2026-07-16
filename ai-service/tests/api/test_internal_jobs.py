"""
Tests for the production internal job API (/internal/*).

Covers auth, validation, idempotency, enqueue, DB-backed status/result, cancel,
and retry. The pipeline is mocked so no external LLM calls happen; the
in-process queue runs jobs synchronously under TestClient.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.progress import progress_store
from app.store.factory import get_stores, reset_stores
from app.store.models import AiJobRecord, JobStatus

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _run(coro):
    """Run an async store coroutine from a sync test (avoids TestClient/loop clash)."""
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
    # Import inside the fixture so app picks up the patched token per test.
    from app.main import app

    return TestClient(app)


@pytest.fixture
def mocked_pipeline():
    mock = MagicMock()
    mock.ainvoke = AsyncMock(
        return_value={
            "status": "completed",
            "job_result": {
                "job_id": "x",
                "status": "completed",
                "requirements": [{"id": "REQ-001", "description": "d"}],
                "user_stories": [{"id": "US-001", "title": "t"}],
                "quality_report": {"overall_score": 0.9},
            },
        }
    )
    with patch("app.main.pipeline", mock):
        yield mock


def _text_job(job_id="job-1", **over):
    body = {
        "job_id": job_id,
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "input_type": "text",
        "content": "The system must let users reset their password by email.",
    }
    body.update(over)
    return body


# --------------------------------------------------------------------------- auth

def test_missing_auth_rejected(client):
    resp = client.post("/internal/jobs", json=_text_job())
    assert resp.status_code == 401


def test_invalid_token_rejected(client):
    resp = client.post("/internal/jobs", headers={"Authorization": "Bearer wrong"}, json=_text_job())
    assert resp.status_code == 403


def test_valid_token_accepted(client, mocked_pipeline):
    resp = client.post("/internal/jobs", headers=AUTH, json=_text_job())
    assert resp.status_code == 202
    assert resp.json()["status"] == "QUEUED"


# --------------------------------------------------------------------- validation

def test_invalid_input_type_rejected(client, mocked_pipeline):
    resp = client.post("/internal/jobs", headers=AUTH, json=_text_job(input_type="bogus"))
    assert resp.status_code == 422  # pydantic Literal validation


def test_text_job_requires_content(client, mocked_pipeline):
    body = _text_job()
    body.pop("content")
    resp = client.post("/internal/jobs", headers=AUTH, json=body)
    assert resp.status_code == 400
    assert "content" in resp.json()["detail"].lower()


def test_backend_document_requires_source_documents(client, mocked_pipeline):
    resp = client.post(
        "/internal/jobs",
        headers=AUTH,
        json={
            "job_id": "doc-1",
            "tenant_id": "t",
            "project_id": "p",
            "input_type": "backend_document",
        },
    )
    assert resp.status_code == 400
    assert "source_documents" in resp.json()["detail"].lower()


def test_bad_job_id_rejected(client, mocked_pipeline):
    resp = client.post("/internal/jobs", headers=AUTH, json=_text_job(job_id="bad id/with space"))
    assert resp.status_code == 400


# ------------------------------------------------------------------ enqueue paths

def test_text_job_enqueued_and_completes(client, mocked_pipeline):
    resp = client.post("/internal/jobs", headers=AUTH, json=_text_job("job-text"))
    assert resp.status_code == 202
    # In-process queue ran it synchronously under TestClient.
    status = client.get("/internal/jobs/job-text", headers=AUTH).json()
    assert status["status"] == JobStatus.COMPLETED.value
    assert status["tenant_id"] == "tenant-1"
    assert "links" in status


def test_backend_document_job_enqueued(client, mocked_pipeline):
    resp = client.post(
        "/internal/jobs",
        headers=AUTH,
        json={
            "job_id": "doc-2",
            "tenant_id": "t",
            "project_id": "p",
            "input_type": "backend_document",
            "source_documents": [{"document_id": "D-1", "mime_type": "application/pdf"}],
        },
    )
    assert resp.status_code == 202


# --------------------------------------------------------------------- idempotency
#
# NOTE: TestClient runs BackgroundTasks synchronously, and `mocked_pipeline`
# resolves immediately, so by the time a second identical POST arrives here the
# first job has already reached COMPLETED. That exercises the "completed job +
# same payload -> 200 idempotent" branch of the matrix. The full status x
# fingerprint decision matrix (including the still-"running" 202 branch, which
# needs a job pre-seeded at QUEUED/PROCESSING to test deterministically without
# racing a synchronous mock) is covered in tests/api/test_job_idempotency.py.

def test_duplicate_job_id_is_idempotent(client, mocked_pipeline):
    first = client.post("/internal/jobs", headers=AUTH, json=_text_job("dup-1"))
    assert first.status_code == 202 and first.json()["idempotent"] is False
    second = client.post("/internal/jobs", headers=AUTH, json=_text_job("dup-1"))
    # First job already completed synchronously -> idempotent 200, per the
    # duplicate-handling matrix (completed + same payload => 200, not 202).
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert second.json()["duplicate_of"] == "dup-1"


# ------------------------------------------------------------------- status/result

def test_result_returns_409_before_completion(client):
    # Create a job directly (QUEUED, not dispatched) → no result yet.
    stores = get_stores()
    _run(stores.jobs.create_job(AiJobRecord(job_id="pending-1", status=JobStatus.QUEUED)))
    resp = client.get("/internal/jobs/pending-1/result", headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["detail"]["status"] == JobStatus.QUEUED.value


def test_result_returns_payload_after_completion(client, mocked_pipeline):
    client.post("/internal/jobs", headers=AUTH, json=_text_job("done-1"))
    resp = client.get("/internal/jobs/done-1/result", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["job_id"] == "done-1" or "requirements" in body


def test_status_unknown_job_404(client):
    assert client.get("/internal/jobs/nope", headers=AUTH).status_code == 404


# --------------------------------------------------------------------------- cancel

def test_cancel_queued_job_marks_cancelled(client):
    stores = get_stores()
    _run(stores.jobs.create_job(AiJobRecord(job_id="cancel-1", status=JobStatus.QUEUED)))
    resp = client.post("/internal/jobs/cancel-1/cancel", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
    rec = _run(stores.jobs.get_job("cancel-1"))
    assert rec.status == JobStatus.CANCELLED


def test_cancel_unknown_job_404(client):
    assert client.post("/internal/jobs/nope/cancel", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------- retry

def test_retry_failed_job_creates_new_attempt(client, mocked_pipeline):
    stores = get_stores()
    _run(stores.jobs.create_job(
        AiJobRecord(job_id="retry-1", status=JobStatus.FAILED, input_type="text", attempt_number=1)
    ))
    resp = client.post("/internal/jobs/retry-1/retry", headers=AUTH)
    assert resp.status_code == 202
    assert resp.json()["attempt_number"] == 2


def test_retry_non_terminal_job_conflicts(client):
    stores = get_stores()
    _run(stores.jobs.create_job(AiJobRecord(job_id="running-1", status=JobStatus.PROCESSING)))
    resp = client.post("/internal/jobs/running-1/retry", headers=AUTH)
    assert resp.status_code == 409


# ---------------------------------------------------------- tracing / no-auth leak

def test_request_id_echoed(client, mocked_pipeline):
    resp = client.post(
        "/internal/jobs", headers={**AUTH, "X-Request-Id": "trace-abc"}, json=_text_job("trace-1")
    )
    assert resp.headers.get("X-Request-Id") == "trace-abc"
