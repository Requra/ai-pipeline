"""
Duplicate-job / idempotency behavior for POST /internal/jobs.

Covers the full status x fingerprint x retry-flag decision matrix: running job
(same/different payload), completed job (same/different payload), failed job
(no retry / explicit retry), cancelled job (different payload), retry=true on
a running job, fingerprint exclusions (callback_url, X-Request-Id) and
inclusions (document identity, content hash, options), concurrent identical
requests, and cross-tenant job_id collisions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import CreateJobRequest
from app.api.service import handle_job_creation
from app.config import settings
from app.progress import progress_store
from app.services.fingerprint import FINGERPRINT_VERSION, compute_job_request_fingerprint
from app.store.factory import get_stores, reset_stores
from app.store.models import AiJobRecord, JobOptions, JobStatus

TOKEN = "test-idem-token"
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
    mock = MagicMock()
    mock.ainvoke = AsyncMock(
        return_value={"status": "completed", "job_result": {"status": "completed", "job_id": "x"}}
    )
    with patch("app.main.pipeline", mock):
        yield mock


def _payload(job_id="idem-1", **over):
    body = {
        "job_id": job_id,
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "input_type": "text",
        "content": "Build a login system that supports password reset.",
    }
    body.update(over)
    return body


def _fingerprint_for(payload: dict) -> str:
    return compute_job_request_fingerprint(CreateJobRequest(**payload))


def _seed(job_id: str, *, status: JobStatus, payload: dict, **over) -> AiJobRecord:
    """Seed an existing job row whose stored fingerprint matches `payload`."""
    stores = get_stores()
    rec = AiJobRecord(
        job_id=job_id,
        tenant_id=payload.get("tenant_id", "tenant-1"),
        project_id=payload.get("project_id", "project-1"),
        status=status,
        options=JobOptions(),
        request_fingerprint=_fingerprint_for(payload),
        request_fingerprint_version=FINGERPRINT_VERSION,
        **over,
    )
    return _run(stores.jobs.create_job(rec))


# ---------------------------------------------------------------------------
# 1. Active job (QUEUED/PROCESSING), same payload -> 202 idempotent, no enqueue
# ---------------------------------------------------------------------------

def test_active_job_same_payload_returns_202_idempotent_no_enqueue(client):
    payload = _payload("active-1")
    _seed("active-1", status=JobStatus.PROCESSING, payload=payload, current_node="extract", progress_pct=45)

    with patch("app.api.internal.dispatch_job", new=AsyncMock()) as mock_dispatch:
        resp = client.post("/internal/jobs", headers=AUTH, json=payload)

    assert resp.status_code == 202
    body = resp.json()
    assert body["idempotent"] is True
    assert body["duplicate_of"] == "active-1"
    assert body["status"] == "PROCESSING"
    assert body["progress_pct"] == 45
    assert body["current_node"] == "extract"
    assert "already running" in body["message"].lower()
    mock_dispatch.assert_not_awaited()

    # No new attempt was created, state wasn't mutated.
    rec = _run(get_stores().jobs.get_job("active-1"))
    assert rec.attempt_number == 1
    assert rec.status == JobStatus.PROCESSING


# ---------------------------------------------------------------------------
# 2. Active job, different payload -> 409, no enqueue, no mutation
# ---------------------------------------------------------------------------

def test_active_job_different_payload_returns_409_no_mutation(client):
    original = _payload("active-2")
    _seed("active-2", status=JobStatus.QUEUED, payload=original)

    different = _payload("active-2", content="A completely different requirement set.")
    with patch("app.api.internal.dispatch_job", new=AsyncMock()) as mock_dispatch:
        resp = client.post("/internal/jobs", headers=AUTH, json=different)

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "JOB_ID_CONFLICT"
    assert body["error"]["existing_status"] == "QUEUED"
    assert body["error"]["job_id"] == "active-2"
    mock_dispatch.assert_not_awaited()

    rec = _run(get_stores().jobs.get_job("active-2"))
    assert rec.status == JobStatus.QUEUED
    assert rec.request_fingerprint == _fingerprint_for(original)  # untouched


# ---------------------------------------------------------------------------
# 3. Concurrent identical requests for a brand-new job_id -> exactly one
#    creation + one enqueue; the other is an idempotent duplicate.
# ---------------------------------------------------------------------------

def test_concurrent_identical_requests_race_safe():
    payload = _payload("concurrent-1")
    req = CreateJobRequest(**payload)

    dispatch_calls = []

    async def maybe_dispatch(outcome):
        if outcome.dispatch:
            dispatch_calls.append(outcome.job.job_id)
        return outcome

    async def one_call():
        outcome = await handle_job_creation(req, job_id="concurrent-1", request_id="r")
        return await maybe_dispatch(outcome)

    async def _both():
        return await asyncio.gather(one_call(), one_call())

    outcomes = _run(_both())

    statuses = sorted(o.http_status for o in outcomes)
    idempotent_flags = sorted(o.body.get("idempotent", False) for o in outcomes)
    assert statuses == [202, 202]
    assert idempotent_flags == [False, True]
    assert len(dispatch_calls) == 1  # exactly one enqueue

    # Exactly one job row; no duplicate attempt was created by the race.
    rec = _run(get_stores().jobs.get_job("concurrent-1"))
    assert rec is not None
    assert rec.attempt_number == 1


# ---------------------------------------------------------------------------
# 4. Completed job, same payload -> 200, result_available, no enqueue
# ---------------------------------------------------------------------------

def test_completed_job_same_payload_returns_200_idempotent(client):
    payload = _payload("done-1")
    _seed("done-1", status=JobStatus.COMPLETED, payload=payload)
    _run(get_stores().results.save_result("done-1", {"job_id": "done-1", "status": "completed"}))

    with patch("app.api.internal.dispatch_job", new=AsyncMock()) as mock_dispatch:
        resp = client.post("/internal/jobs", headers=AUTH, json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["idempotent"] is True
    assert body["result_available"] is True
    assert body["links"]["result"] == "/internal/jobs/done-1/result"
    mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. Completed job, different payload -> 409
# ---------------------------------------------------------------------------

def test_completed_job_different_payload_returns_409(client):
    original = _payload("done-2")
    _seed("done-2", status=JobStatus.COMPLETED, payload=original)

    different = _payload("done-2", content="Some other requirement entirely.")
    resp = client.post("/internal/jobs", headers=AUTH, json=different)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "JOB_ID_CONFLICT"


# ---------------------------------------------------------------------------
# 6. Failed job, same payload, no retry -> report failed status, no enqueue
# ---------------------------------------------------------------------------

def test_failed_job_same_payload_no_retry_reports_status(client):
    payload = _payload("failed-1")
    _seed("failed-1", status=JobStatus.FAILED, payload=payload)

    with patch("app.api.internal.dispatch_job", new=AsyncMock()) as mock_dispatch:
        resp = client.post("/internal/jobs", headers=AUTH, json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["idempotent"] is True
    assert "retry" in body["message"].lower()
    mock_dispatch.assert_not_awaited()

    rec = _run(get_stores().jobs.get_job("failed-1"))
    assert rec.status == JobStatus.FAILED
    assert rec.attempt_number == 1  # no new attempt


# ---------------------------------------------------------------------------
# 7. Failed job, same payload, explicit retry -> new attempt, one enqueue, 202
# ---------------------------------------------------------------------------

def test_failed_job_same_payload_explicit_retry_creates_new_attempt(client, mocked_pipeline):
    payload = _payload("failed-2")
    _seed("failed-2", status=JobStatus.FAILED, payload=payload)

    retry_payload = dict(payload, reprocess=True)
    resp = client.post("/internal/jobs", headers=AUTH, json=retry_payload)

    assert resp.status_code == 202
    body = resp.json()
    assert body["attempt_number"] == 2
    assert body["retried"] is True

    rec = _run(get_stores().jobs.get_job("failed-2"))
    assert rec.attempt_number == 2
    # Job actually ran (mocked pipeline resolves synchronously under TestClient).
    assert rec.status == JobStatus.COMPLETED


# ---------------------------------------------------------------------------
# 8. Running job, retry=true -> rejected, no enqueue
# ---------------------------------------------------------------------------

def test_running_job_retry_true_is_rejected(client):
    payload = _payload("running-retry-1")
    _seed("running-retry-1", status=JobStatus.PROCESSING, payload=payload)

    retry_payload = dict(payload, reprocess=True)
    with patch("app.api.internal.dispatch_job", new=AsyncMock()) as mock_dispatch:
        resp = client.post("/internal/jobs", headers=AUTH, json=retry_payload)

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "JOB_NOT_RETRYABLE"
    assert body["error"]["existing_status"] == "PROCESSING"
    mock_dispatch.assert_not_awaited()

    rec = _run(get_stores().jobs.get_job("running-retry-1"))
    assert rec.status == JobStatus.PROCESSING
    assert rec.attempt_number == 1


# ---------------------------------------------------------------------------
# 9. Cancelled job, different payload -> 409
# ---------------------------------------------------------------------------

def test_cancelled_job_different_payload_returns_409(client):
    original = _payload("cancelled-1")
    _seed("cancelled-1", status=JobStatus.CANCELLED, payload=original)

    different = _payload("cancelled-1", content="A different input for the same job_id.")
    resp = client.post("/internal/jobs", headers=AUTH, json=different)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "JOB_ID_CONFLICT"


def test_cancelled_job_same_payload_no_retry_reports_status(client):
    payload = _payload("cancelled-2")
    _seed("cancelled-2", status=JobStatus.CANCELLED, payload=payload)

    with patch("app.api.internal.dispatch_job", new=AsyncMock()) as mock_dispatch:
        resp = client.post("/internal/jobs", headers=AUTH, json=payload)

    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"
    assert resp.json()["idempotent"] is True
    mock_dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# 10. Fingerprint excludes callback_url and request_id (still idempotent)
# ---------------------------------------------------------------------------

def test_different_callback_url_is_still_idempotent(client):
    payload = _payload("cb-diff-1")
    _seed("cb-diff-1", status=JobStatus.PROCESSING, payload=payload)

    resubmit = _payload("cb-diff-1", options={"callback_url": "https://backend.example/new-callback"})
    resp = client.post("/internal/jobs", headers=AUTH, json=resubmit)
    assert resp.status_code == 202
    assert resp.json()["idempotent"] is True


def test_different_request_id_header_is_still_idempotent(client):
    payload = _payload("req-id-diff-1")
    _seed("req-id-diff-1", status=JobStatus.PROCESSING, payload=payload)

    resp = client.post(
        "/internal/jobs", headers={**AUTH, "X-Request-Id": "some-other-trace-id"}, json=payload
    )
    assert resp.status_code == 202
    assert resp.json()["idempotent"] is True


# ---------------------------------------------------------------------------
# 11. Fingerprint includes actual input identity -> conflicts on real changes
# ---------------------------------------------------------------------------

def test_different_document_id_conflicts(client):
    payload = _payload(
        "doc-diff-1", input_type="backend_document", content=None,
        source_documents=[{"document_id": "D-1", "mime_type": "application/pdf"}],
    )
    _seed("doc-diff-1", status=JobStatus.PROCESSING, payload=payload)

    different = _payload(
        "doc-diff-1", input_type="backend_document", content=None,
        source_documents=[{"document_id": "D-2", "mime_type": "application/pdf"}],
    )
    resp = client.post("/internal/jobs", headers=AUTH, json=different)
    assert resp.status_code == 409


def test_different_content_hash_conflicts(client):
    payload = _payload("content-diff-1", content="Original requirement text.")
    _seed("content-diff-1", status=JobStatus.PROCESSING, payload=payload)

    different = _payload("content-diff-1", content="Materially different requirement text.")
    resp = client.post("/internal/jobs", headers=AUTH, json=different)
    assert resp.status_code == 409


def test_different_important_option_conflicts(client):
    payload = _payload("option-diff-1")
    _seed("option-diff-1", status=JobStatus.PROCESSING, payload=payload)

    different = _payload("option-diff-1", options={"enable_hybrid_retrieval": True})
    resp = client.post("/internal/jobs", headers=AUTH, json=different)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 12. Tenant safety: same job_id under a different tenant never leaks status
# ---------------------------------------------------------------------------

def test_cross_tenant_job_id_collision_returns_safe_conflict(client):
    original = _payload("shared-id-1", tenant_id="tenant-A", project_id="project-A")
    _seed("shared-id-1", status=JobStatus.PROCESSING, payload=original,
          progress_pct=77, current_node="generate")

    other_tenant_request = _payload("shared-id-1", tenant_id="tenant-B", project_id="project-B")
    with patch("app.api.internal.dispatch_job", new=AsyncMock()) as mock_dispatch:
        resp = client.post("/internal/jobs", headers=AUTH, json=other_tenant_request)

    assert resp.status_code == 409
    body = resp.json()
    # No leakage: no existing_status, progress, tenant, or other identifying
    # detail about tenant-A's job is present in the response to tenant-B.
    assert body["error"]["code"] == "JOB_ID_CONFLICT"
    assert "existing_status" not in body["error"]
    raw = resp.text
    assert "PROCESSING" not in raw
    assert "generate" not in raw
    assert "tenant-A" not in raw
    mock_dispatch.assert_not_awaited()

    # tenant-A's job is untouched.
    rec = _run(get_stores().jobs.get_job("shared-id-1"))
    assert rec.tenant_id == "tenant-A"
    assert rec.status == JobStatus.PROCESSING
