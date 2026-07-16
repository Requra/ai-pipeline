"""
Tests for deterministic job-request fingerprinting (app.services.fingerprint).

Covers: determinism, exclusion of volatile/transport fields (callback_url,
priority, retry flag), inclusion of identity-bearing fields (tenant/project,
input identity, relevant options), content normalization (CRLF, outer
whitespace, case preserved), and source_document order-independence.
"""

from __future__ import annotations

from app.api.schemas import CreateJobRequest
from app.services.fingerprint import FINGERPRINT_VERSION, compute_job_request_fingerprint


def _req(**over):
    base = dict(
        job_id="j1",
        tenant_id="tenant-1",
        project_id="project-1",
        input_type="text",
        content="Build a login system.",
    )
    base.update(over)
    return CreateJobRequest(**base)


def test_fingerprint_is_deterministic():
    r = _req()
    assert compute_job_request_fingerprint(r) == compute_job_request_fingerprint(r)
    # A fresh, separately-constructed but identical request hashes the same.
    assert compute_job_request_fingerprint(_req()) == compute_job_request_fingerprint(r)


def test_fingerprint_excludes_callback_url():
    a = _req(options={"callback_url": "https://a.example/cb"})
    b = _req(options={"callback_url": "https://b.example/other"})
    assert compute_job_request_fingerprint(a) == compute_job_request_fingerprint(b)


def test_fingerprint_excludes_priority():
    a = _req(options={"priority": "low"})
    b = _req(options={"priority": "high"})
    assert compute_job_request_fingerprint(a) == compute_job_request_fingerprint(b)


def test_fingerprint_excludes_job_id_and_reprocess_flag():
    # job_id is the lookup key, not payload identity; reprocess is a transport
    # instruction. Neither should perturb the fingerprint.
    a = _req(job_id="job-a", reprocess=False)
    b = _req(job_id="job-b", reprocess=True)
    assert compute_job_request_fingerprint(a) == compute_job_request_fingerprint(b)


def test_fingerprint_changes_with_tenant_or_project():
    base = _req()
    other_tenant = _req(tenant_id="tenant-2")
    other_project = _req(project_id="project-2")
    fp = compute_job_request_fingerprint(base)
    assert compute_job_request_fingerprint(other_tenant) != fp
    assert compute_job_request_fingerprint(other_project) != fp


def test_fingerprint_changes_with_content():
    a = _req(content="Build a login system.")
    b = _req(content="Build a totally different system.")
    assert compute_job_request_fingerprint(a) != compute_job_request_fingerprint(b)


def test_fingerprint_normalizes_line_endings_and_outer_whitespace():
    a = _req(content="Build a login system.")
    b = _req(content="  Build a login system.\r\n")
    assert compute_job_request_fingerprint(a) == compute_job_request_fingerprint(b)


def test_fingerprint_does_not_lowercase_content():
    a = _req(content="Build a login system.")
    b = _req(content="BUILD A LOGIN SYSTEM.")
    assert compute_job_request_fingerprint(a) != compute_job_request_fingerprint(b)


def test_fingerprint_changes_with_relevant_options():
    base = _req()
    variants = [
        _req(options={"enable_embeddings": True}),
        _req(options={"enable_hybrid_retrieval": True}),
        _req(options={"generate_user_stories": False}),
        _req(options={"generate_summary": False}),
        _req(options={"language": "fr"}),
    ]
    fp = compute_job_request_fingerprint(base)
    for v in variants:
        assert compute_job_request_fingerprint(v) != fp


def test_fingerprint_changes_with_source_document_identity():
    a = _req(
        input_type="backend_document",
        content=None,
        source_documents=[{"document_id": "D-1", "mime_type": "application/pdf"}],
    )
    b = _req(
        input_type="backend_document",
        content=None,
        source_documents=[{"document_id": "D-2", "mime_type": "application/pdf"}],
    )
    assert compute_job_request_fingerprint(a) != compute_job_request_fingerprint(b)


def test_fingerprint_changes_with_source_document_hash():
    a = _req(
        input_type="backend_document", content=None,
        source_documents=[{"document_id": "D-1", "sha256_hash": "aaa"}],
    )
    b = _req(
        input_type="backend_document", content=None,
        source_documents=[{"document_id": "D-1", "sha256_hash": "bbb"}],
    )
    assert compute_job_request_fingerprint(a) != compute_job_request_fingerprint(b)


def test_fingerprint_source_documents_order_independent():
    a = _req(
        input_type="backend_document", content=None,
        source_documents=[{"document_id": "D-1"}, {"document_id": "D-2"}],
    )
    b = _req(
        input_type="backend_document", content=None,
        source_documents=[{"document_id": "D-2"}, {"document_id": "D-1"}],
    )
    assert compute_job_request_fingerprint(a) == compute_job_request_fingerprint(b)


def test_fingerprint_source_documents_with_repeated_ids_are_order_independent():
    a = _req(
        input_type="backend_document", content=None,
        source_documents=[
            {"document_id": "D-1", "sha256_hash": "aaa"},
            {"document_id": "D-1", "sha256_hash": "bbb"},
        ],
    )
    b = _req(
        input_type="backend_document", content=None,
        source_documents=[
            {"document_id": "D-1", "sha256_hash": "bbb"},
            {"document_id": "D-1", "sha256_hash": "aaa"},
        ],
    )
    assert compute_job_request_fingerprint(a) == compute_job_request_fingerprint(b)


def test_fingerprint_changes_with_requested_by():
    a = _req(requested_by="user-1")
    b = _req(requested_by="user-2")
    assert compute_job_request_fingerprint(a) != compute_job_request_fingerprint(b)


def test_fingerprint_version_is_stable_constant():
    assert isinstance(FINGERPRINT_VERSION, int) and FINGERPRINT_VERSION >= 1
