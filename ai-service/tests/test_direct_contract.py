"""
Focused tests for the harden/direct-demo-contract changes.

These cover:

  * /process-json returns 202 and a stable QUEUED contract.
  * /status/{job_id} returns the documented contract shape and 404 on miss.
  * Empty content on /process-json → 400 (not 500).
  * Invalid metadata JSON on /process → 400 (not 500).
  * Unsupported content-type → 415.
  * uuid4 job ids are unique across two /process calls with the same filename.
  * progress.cleanup_expired_jobs prunes stale entries.

Existing tests in test_polling.py / test_contract_v1.py / test_e2e_mocked.py
remain authoritative for the end-to-end pipeline behaviour. This file is
scoped to the API contract surface only.
"""

from __future__ import annotations

import io
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import progress as progress_module
from app.main import app
from app.progress import cleanup_expired_jobs, progress_store, update_progress


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_progress_store():
    """Keep tests independent — the progress store is module-level state."""
    progress_store.clear()
    yield
    progress_store.clear()


@pytest.fixture
def mocked_pipeline():
    """Patch the pipeline so /process-json doesn't hit any external API."""
    mock = MagicMock()
    mock.ainvoke = AsyncMock(
        return_value={
            "status": "success",
            "job_result": {"dummy": "result"},
        }
    )
    with patch("app.main.pipeline", mock):
        yield mock


# ---------------------------------------------------------------------------
# /process-json — happy path + QUEUED contract
# ---------------------------------------------------------------------------

def test_process_json_returns_queued_contract(mocked_pipeline):
    client = TestClient(app)
    payload = {
        "job_id": "queued-shape-job",
        "content": "Build a system that records refund requests.",
        "metadata": {},
    }

    resp = client.post("/process-json", json=payload)

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == "queued-shape-job"
    assert body["status"] == "QUEUED"
    # No surprise keys leak from the request body.
    assert set(body.keys()) == {"job_id", "status"}


def test_process_json_generates_job_id_when_omitted(mocked_pipeline):
    client = TestClient(app)

    resp = client.post(
        "/process-json",
        json={"content": "Some software requirement text."},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["job_id"].startswith("text_")
    # uuid4 hex is 32 chars → "text_" + 32 = 37.
    assert len(body["job_id"]) == len("text_") + 32


# ---------------------------------------------------------------------------
# /process-json — input validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_content", ["", "   ", "\n\t\n"])
def test_process_json_empty_content_returns_400(bad_content, mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process-json",
        json={"content": bad_content, "metadata": {}},
    )
    assert resp.status_code == 400
    assert "content" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /status/{job_id}
# ---------------------------------------------------------------------------

def test_status_returns_full_contract_shape():
    update_progress(
        job_id="shape-test-job",
        node_name="generate",
        progress_pct=42,
        status="PROCESSING",
    )

    client = TestClient(app)
    resp = client.get("/status/shape-test-job")
    assert resp.status_code == 200

    body = resp.json()
    expected_keys = {
        "job_id",
        "status",
        "progress_pct",
        "current_node",
        "result",
        "error",
        "created_at",
        "updated_at",
        "completed_at",
    }
    assert expected_keys.issubset(body.keys())
    assert body["job_id"] == "shape-test-job"
    assert body["status"] == "PROCESSING"
    assert body["progress_pct"] == 42
    assert body["current_node"] == "generate"
    assert body["result"] is None
    assert body["error"] is None
    assert body["completed_at"] is None
    assert body["created_at"] <= body["updated_at"]


def test_status_404_when_job_missing():
    client = TestClient(app)
    resp = client.get("/status/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Job not found"


def test_terminal_status_sets_completed_at():
    update_progress(
        job_id="terminal-job",
        node_name="format",
        progress_pct=100,
        status="COMPLETED",
        result={"hello": "world"},
    )
    client = TestClient(app)
    body = client.get("/status/terminal-job").json()
    assert body["status"] == "COMPLETED"
    assert body["completed_at"] is not None
    assert body["result"] == {"hello": "world"}


# ---------------------------------------------------------------------------
# /process — multipart contract
# ---------------------------------------------------------------------------

def _txt_upload(name: str = "doc.txt", body: bytes = b"hello world"):
    return ("file", (name, io.BytesIO(body), "text/plain"))


def test_process_multipart_invalid_metadata_returns_400(mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process",
        files=[_txt_upload()],
        data={"metadata": "{not valid json", "file_type": "document"},
    )
    assert resp.status_code == 400
    assert "metadata" in resp.json()["detail"].lower()


def test_process_multipart_unsupported_content_type_returns_415(mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process",
        files=[("file", ("evil.exe", io.BytesIO(b"MZ"), "application/x-msdownload"))],
        data={"metadata": "{}"},
    )
    assert resp.status_code == 415


def test_process_multipart_uses_uuid_for_job_id(mocked_pipeline):
    client = TestClient(app)
    first = client.post(
        "/process",
        files=[_txt_upload("same.txt", b"first body")],
        data={"metadata": "{}"},
    )
    second = client.post(
        "/process",
        files=[_txt_upload("same.txt", b"second body")],
        data={"metadata": "{}"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    # uuid4 ensures uniqueness even with the same filename within the same
    # wall-second — the legacy hash()+time() id could collide.
    assert first.json()["job_id"] != second.json()["job_id"]
    assert first.json()["job_id"].startswith("upload_")


def test_process_multipart_empty_file_returns_400(mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process",
        files=[_txt_upload("empty.txt", b"")],
        data={"metadata": "{}"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# progress.cleanup_expired_jobs
# ---------------------------------------------------------------------------

def test_cleanup_expired_jobs_prunes_old_terminal_entries():
    update_progress(
        job_id="old-terminal",
        node_name="format",
        progress_pct=100,
        status="COMPLETED",
    )
    update_progress(
        job_id="fresh-terminal",
        node_name="format",
        progress_pct=100,
        status="COMPLETED",
    )
    update_progress(
        job_id="active",
        node_name="generate",
        progress_pct=50,
        status="PROCESSING",
    )

    # Backdate the old terminal entry past the TTL window.
    progress_store["old-terminal"]["completed_at"] = time.time() - 7200

    pruned = cleanup_expired_jobs(ttl_seconds=3600)

    assert pruned == 1
    assert "old-terminal" not in progress_store
    assert "fresh-terminal" in progress_store
    assert "active" in progress_store


def test_cleanup_expired_jobs_ttl_zero_is_noop():
    update_progress(
        job_id="any-job",
        node_name="format",
        progress_pct=100,
        status="COMPLETED",
    )
    assert cleanup_expired_jobs(ttl_seconds=0) == 0
    assert "any-job" in progress_store


# ---------------------------------------------------------------------------
# Module structure assertion — progress.update_progress accepts the same
# signature the rest of the codebase expects.
# ---------------------------------------------------------------------------

def test_update_progress_creates_full_entry():
    update_progress(
        job_id="entry-shape",
        node_name="ingest",
        progress_pct=10,
        status="PROCESSING",
    )
    entry = progress_store["entry-shape"]
    for required in (
        "job_id",
        "status",
        "progress_pct",
        "current_node",
        "result",
        "error",
        "created_at",
        "updated_at",
        "completed_at",
    ):
        assert required in entry, f"missing key: {required}"


# Keep the import used (sanity that the module loads cleanly with the new
# changes; if app/progress.py breaks, this fails fast).
assert progress_module is not None
