import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.store.factory import get_stores, reset_stores
from app.store.models import JobStatus

FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "test-fixtures" / "e2e_real_mixed"
)
TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "AI_INTERNAL_SERVICE_TOKEN", TOKEN)
    reset_stores()
    yield
    reset_stores()


@pytest.fixture
def pdf_bytes():
    return (FIXTURES_DIR / "requirements.pdf").read_bytes()


@pytest.fixture
def txt_bytes():
    return (FIXTURES_DIR / "stakeholder-notes.txt").read_bytes()


@pytest.fixture
def docx_bytes():
    return (FIXTURES_DIR / "technical-notes.docx").read_bytes()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mocked_pipeline():
    mock = MagicMock()
    mock.ainvoke = AsyncMock(
        return_value={
            "status": "completed",
            "job_result": {
                "job_id": "test-job",
                "status": "completed",
                "requirements": [],
                "user_stories": [],
                "quality_report": {"overall_score": 1.0},
            },
        }
    )
    with patch("app.main.pipeline", mock):
        yield mock


# ---------------------------------------------------------------------------
# 1. Multipart & Validation Tests for POST /process
# ---------------------------------------------------------------------------


def test_single_pdf_upload(client, pdf_bytes, mocked_pipeline):
    files = [("file", ("requirements.pdf", pdf_bytes, "application/pdf"))]
    resp = client.post("/process", files=files)
    assert resp.status_code == 202
    assert "job_id" in resp.json()
    assert resp.json()["status"] == "QUEUED"


def test_single_docx_upload(client, docx_bytes, mocked_pipeline):
    files = [
        (
            "file",
            (
                "technical-notes.docx",
                docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
    ]
    resp = client.post("/process", files=files)
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_single_txt_upload(client, txt_bytes, mocked_pipeline):
    files = [("file", ("stakeholder-notes.txt", txt_bytes, "text/plain"))]
    resp = client.post("/process", files=files)
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_repeated_files_all_three_documents(
    client, pdf_bytes, txt_bytes, docx_bytes, mocked_pipeline
):
    files = [
        ("files", ("requirements.pdf", pdf_bytes, "application/pdf")),
        ("files", ("stakeholder-notes.txt", txt_bytes, "text/plain")),
        (
            "files",
            (
                "technical-notes.docx",
                docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        ),
    ]
    resp = client.post("/process", files=files)
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "QUEUED"
    assert "job_id" in data


def test_repeated_document_ids_mapping(client, pdf_bytes, txt_bytes, mocked_pipeline):
    files = [
        ("files", ("requirements.pdf", pdf_bytes, "application/pdf")),
        ("files", ("stakeholder-notes.txt", txt_bytes, "text/plain")),
    ]
    data = {"document_ids": ["doc_pdf_custom", "doc_txt_custom"]}
    resp = client.post("/process", files=files, data=data)
    assert resp.status_code == 202


def test_reject_both_file_and_files(client, pdf_bytes, txt_bytes, mocked_pipeline):
    files = [
        ("file", ("requirements.pdf", pdf_bytes, "application/pdf")),
        ("files", ("stakeholder-notes.txt", txt_bytes, "text/plain")),
    ]
    resp = client.post("/process", files=files)
    assert resp.status_code == 400
    assert "not both" in resp.json()["detail"]


def test_reject_both_document_id_and_document_ids(client, pdf_bytes, mocked_pipeline):
    files = [("file", ("requirements.pdf", pdf_bytes, "application/pdf"))]
    data = {"document_id": "doc_1", "document_ids": ["doc_1"]}
    resp = client.post("/process", files=files, data=data)
    assert resp.status_code == 400
    assert "not both" in resp.json()["detail"]


def test_reject_mismatched_file_and_document_id_counts(
    client, pdf_bytes, txt_bytes, mocked_pipeline
):
    files = [
        ("files", ("requirements.pdf", pdf_bytes, "application/pdf")),
        ("files", ("stakeholder-notes.txt", txt_bytes, "text/plain")),
    ]
    data = {"document_ids": ["only_one_id"]}
    resp = client.post("/process", files=files, data=data)
    assert resp.status_code == 400
    assert "once for each uploaded file" in resp.json()["detail"]


def test_reject_duplicate_document_ids(client, pdf_bytes, txt_bytes, mocked_pipeline):
    files = [
        ("files", ("requirements.pdf", pdf_bytes, "application/pdf")),
        ("files", ("stakeholder-notes.txt", txt_bytes, "text/plain")),
    ]
    data = {"document_ids": ["same_doc_id", "same_doc_id"]}
    resp = client.post("/process", files=files, data=data)
    assert resp.status_code == 400
    assert "unique document ID" in resp.json()["detail"]


def test_reject_empty_file(client, mocked_pipeline):
    files = [("file", ("empty.txt", b"", "text/plain"))]
    resp = client.post("/process", files=files)
    assert resp.status_code == 400
    assert "is empty" in resp.json()["detail"]


def test_reject_unsupported_file_signature(client, mocked_pipeline):
    files = [("file", ("fake.pdf", b"NOT_A_REAL_PDF_HEADER_AT_ALL", "application/pdf"))]
    resp = client.post("/process", files=files)
    assert resp.status_code == 415
    assert "unsupported media type" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2. Internal /internal/process Compatibility Endpoint Tests
# ---------------------------------------------------------------------------


def test_internal_process_repeated_files(
    client, pdf_bytes, txt_bytes, docx_bytes, mocked_pipeline
):
    files = [
        ("files", ("requirements.pdf", pdf_bytes, "application/pdf")),
        ("files", ("stakeholder-notes.txt", txt_bytes, "text/plain")),
        (
            "files",
            (
                "technical-notes.docx",
                docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        ),
    ]
    data = {
        "job_id": "test_internal_job_multi_001",
        "project_id": "proj_internal_test",
        "document_ids": ["doc_pdf_1", "doc_txt_1", "doc_docx_1"],
    }
    resp = client.post("/internal/process", headers=AUTH, files=files, data=data)
    assert resp.status_code == 202
    assert resp.json()["job_id"] == "test_internal_job_multi_001"
    assert resp.json()["status"] == "QUEUED"


def test_internal_process_legacy_singular_file(client, pdf_bytes, mocked_pipeline):
    files = [("file", ("requirements.pdf", pdf_bytes, "application/pdf"))]
    data = {
        "job_id": "test_internal_job_single_001",
        "project_id": "proj_internal_test",
        "document_id": "doc_pdf_single",
    }
    resp = client.post("/internal/process", headers=AUTH, files=files, data=data)
    assert resp.status_code == 202
    assert resp.json()["job_id"] == "test_internal_job_single_001"


# ---------------------------------------------------------------------------
# 3. Source Completeness & Multi-Document Integration Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_documents_persist_sources_and_chunks(
    pdf_bytes, txt_bytes, docx_bytes
):
    """Verify that all 3 sources create distinct source records and chunks in PostgreSQL/Store."""
    job_id = "test_persistence_3docs_verify"

    raw_inputs = [
        {
            "document_id": "doc_pdf",
            "filename": "requirements.pdf",
            "file_type": "pdf",
            "mime_type": "application/pdf",
            "raw_bytes": pdf_bytes,
        },
        {
            "document_id": "doc_txt",
            "filename": "stakeholder-notes.txt",
            "file_type": "text",
            "mime_type": "text/plain",
            "raw_bytes": txt_bytes,
        },
        {
            "document_id": "doc_docx",
            "filename": "technical-notes.docx",
            "file_type": "docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "raw_bytes": docx_bytes,
        },
    ]

    from app.nodes.prepare_sources import prepare_sources_node

    state = {
        "job_id": job_id,
        "raw_inputs": raw_inputs,
        "source_documents": [
            {
                "document_id": i["document_id"],
                "filename": i["filename"],
                "file_type": i["file_type"],
            }
            for i in raw_inputs
        ],
        "language": "en",
    }

    from app.services.source_processing.extractors import RelevanceCheckResult

    with patch(
        "app.services.source_processing.document._run_relevance_check",
        AsyncMock(
            return_value=RelevanceCheckResult(is_useful=True, relevance_score=0.95)
        ),
    ):
        result = await prepare_sources_node(state)

    assert result["status"] == "sources_prepared"
    assert result["is_useful"] is True
    assert result["partial_source_failure"] is False

    # Assert all 3 sources generated chunks
    chunks = result["chunks"]
    assert len(chunks) >= 3
    doc_ids_in_chunks = {c.document_id for c in chunks}
    assert doc_ids_in_chunks == {"doc_pdf", "doc_txt", "doc_docx"}

    # Assert source document manifest has all 3 documents
    source_docs = result["source_documents"]
    assert len(source_docs) == 3
    filenames = {d["filename"] for d in source_docs}
    assert filenames == {
        "requirements.pdf",
        "stakeholder-notes.txt",
        "technical-notes.docx",
    }
