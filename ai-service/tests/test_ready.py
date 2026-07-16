"""
Phase 1 — readiness probe and job-id hardening tests.

Covers:
  * GET /ready returns safe diagnostics (200 when LLM provider usable, 503 when
    not) and never leaks key material.
  * Caller-supplied job ids are validated (/process-json + /process metadata).
  * sanitize_job_id accepts safe ids and rejects unsafe ones.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.progress import progress_store
from app.services.job_store import (
    JOB_ID_MAX_LEN,
    MemoryJobStore,
    sanitize_job_id,
)


@pytest.fixture(autouse=True)
def _clear_progress_store():
    progress_store.clear()
    yield
    progress_store.clear()


@pytest.fixture
def mocked_pipeline():
    mock = MagicMock()
    mock.ainvoke = AsyncMock(
        return_value={"status": "success", "job_result": {"dummy": "result"}}
    )
    with patch("app.main.pipeline", mock):
        yield mock


# ---------------------------------------------------------------------------
# /ready
# ---------------------------------------------------------------------------

def test_ready_returns_safe_diagnostics_shape():
    client = TestClient(app)
    resp = client.get("/ready")
    # 200 (provider configured) or 503 (degraded) — both are valid responses.
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "ready" in body
    assert "checks" in body
    assert "llm" in body["checks"]
    assert "transcription" in body["checks"]
    llm = body["checks"]["llm"]
    assert set(llm.keys()) >= {"ok", "provider", "api_key_present"}
    # api_key_present must be a boolean, never the actual key.
    assert isinstance(llm["api_key_present"], bool)


def test_ready_never_leaks_key_material():
    client = TestClient(app)
    raw = client.get("/ready").text.lower()
    # No raw secret material should appear anywhere in the payload.
    for needle in ("sk-", "api_key=", "secret", "bearer "):
        assert needle not in raw


def test_ready_503_when_provider_unsupported():
    # Patch the report builder's view of settings via the startup module.
    with patch("app.startup.settings") as fake_settings:
        fake_settings.LLM_PROVIDER = "no-such-provider"
        fake_settings.TRANSCRIBE_PROVIDER = "groq"
        fake_settings.ENV = "production"
        fake_settings.OPENROUTER_API_KEY = None
        fake_settings.OPENAI_API_KEY = None
        fake_settings.GROQ_API_KEY = None
        fake_settings.DEEPGRAM_API_KEY = None
        client = TestClient(app)
        resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False


# ---------------------------------------------------------------------------
# sanitize_job_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "good",
    ["abc", "job_123", "text-9f8a", "A.B.C", "x" * JOB_ID_MAX_LEN, "  trimmed-me  "],
)
def test_sanitize_job_id_accepts_safe(good):
    out = sanitize_job_id(good)
    assert out == good.strip()


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "has space", "slash/evil", "semi;colon", "x" * (JOB_ID_MAX_LEN + 1), "a\nb", "../etc"],
)
def test_sanitize_job_id_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        sanitize_job_id(bad)


def test_sanitize_job_id_rejects_non_string():
    with pytest.raises(ValueError):
        sanitize_job_id(12345)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Caller-supplied job ids through the API
# ---------------------------------------------------------------------------

def test_process_json_rejects_unsafe_job_id(mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process-json",
        json={"job_id": "evil id/with spaces", "content": "Some requirement text."},
    )
    assert resp.status_code == 400
    assert "job_id" in resp.json()["detail"].lower()


def test_process_json_accepts_safe_backend_job_id(mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process-json",
        json={"job_id": "backend-job-42", "content": "Some requirement text."},
    )
    assert resp.status_code == 202
    assert resp.json()["job_id"] == "backend-job-42"


def _txt_upload(name: str = "doc.txt", body: bytes = b"hello world for the pipeline"):
    return ("file", (name, io.BytesIO(body), "text/plain"))


def test_process_multipart_accepts_safe_job_id_from_metadata(mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process",
        files=[_txt_upload()],
        data={"metadata": '{"job_id": "backend-upload-7"}'},
    )
    assert resp.status_code == 202
    assert resp.json()["job_id"] == "backend-upload-7"


def test_process_multipart_rejects_unsafe_job_id_from_metadata(mocked_pipeline):
    client = TestClient(app)
    resp = client.post(
        "/process",
        files=[_txt_upload()],
        data={"metadata": '{"job_id": "bad id with spaces"}'},
    )
    assert resp.status_code == 400
    assert "job_id" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# MemoryJobStore shares state with app.progress (single source of truth)
# ---------------------------------------------------------------------------

def test_memory_job_store_shares_progress_store():
    store = MemoryJobStore()
    store.create("store-job-1")
    store.update("store-job-1", "extract", 45, "PROCESSING")
    # Visible through the legacy dict.
    assert progress_store["store-job-1"]["current_node"] == "extract"
    # Typed view round-trips the public shape.
    status = store.get_status("store-job-1")
    assert status is not None
    assert status.job_id == "store-job-1"
    assert status.progress_pct == 45
    assert store.get_status("missing") is None
