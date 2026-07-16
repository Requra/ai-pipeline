"""
Deterministic request fingerprinting for job idempotency.

``POST /internal/jobs`` is idempotent by ``job_id`` — but ``job_id`` alone
cannot tell us whether a second request with the same id is a genuine retry of
the *same* logical request (safe to no-op / return the existing job) or an
accidental reuse of a job_id for a *different* request (must be rejected, since
silently processing it would either corrupt the running job or misattribute a
different input's results to the original job_id).

``compute_job_request_fingerprint`` hashes only the *production-relevant*,
non-volatile parts of the request: tenant/project/requester, input type, the
identity of the source documents or a hash of inline text content, and the
processing options that change pipeline behavior. It deliberately excludes
transport/operational fields (``callback_url``, ``priority``, request tracing,
timestamps, the retry flag itself) so that changing those does not change the
fingerprint — two requests that differ only in ``callback_url`` are still the
"same" logical job.

Raw document/transcript text is never part of the fingerprint or logged
anywhere — only its SHA-256 digest is used.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

# Bump when the normalization rules change in a way that would alter the
# fingerprint for otherwise-identical requests, so stored fingerprints can be
# recognized as computed under an older scheme if ever needed.
FINGERPRINT_VERSION = 1


def _normalize_content(content: str) -> str:
    """Trim outer whitespace and normalize line endings — never change case."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip()


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _stable_doc_key(doc: Any) -> str:
    """Sort key for a source document: prefer document_id, then storage/file id."""
    return (
        getattr(doc, "document_id", None)
        or getattr(doc, "storage_key", None)
        or getattr(doc, "file_url", None)
        or ""
    )


def _normalize_source_documents(source_documents: Optional[List[Any]]) -> List[Dict[str, Any]]:
    docs = source_documents or []
    normalized = [
        {
            "document_id": getattr(d, "document_id", None),
            # A stable file identifier: storage_key when present, else file_url.
            # Missing optional fields normalize to None (never omitted), so two
            # documents that differ only in an absent field still hash the same.
            "storage_key": getattr(d, "storage_key", None) or getattr(d, "file_url", None),
            "sha256_hash": getattr(d, "hash", None) or getattr(d, "sha256_hash", None),
            "mime_type": getattr(d, "mime_type", None),
            "file_type": getattr(d, "file_type", None),
        }
        for d in docs
    ]
    normalized.sort(key=lambda x: (x["document_id"] or "", x["storage_key"] or ""))
    return normalized


def _normalize_options(options: Any) -> Dict[str, Any]:
    """Only the options that change pipeline *behavior* are fingerprinted.

    ``callback_url`` and ``priority`` are transport/operational and explicitly
    excluded — changing them must not change the fingerprint.
    """
    return {
        "generate_user_stories": bool(getattr(options, "generate_user_stories", True)),
        "generate_summary": bool(getattr(options, "generate_summary", True)),
        "enable_embeddings": bool(getattr(options, "enable_embeddings", False)),
        "enable_hybrid_retrieval": bool(getattr(options, "enable_hybrid_retrieval", False)),
        "language": getattr(options, "language", "en"),
    }


def compute_job_request_fingerprint(request: Any) -> str:
    """Compute a deterministic SHA-256 fingerprint for a ``CreateJobRequest``.

    ``request`` duck-types ``CreateJobRequest`` (tenant_id, project_id,
    requested_by/user_id, input_type, content, source_documents, options). The
    ``job_id`` itself and the ``reprocess``/retry flag are intentionally NOT
    part of the fingerprint: job_id is the lookup key (not the payload
    identity), and the retry flag is a transport instruction, not an input.
    """
    content = getattr(request, "content", None)
    content_sha256 = _sha256_hex(_normalize_content(content)) if content and content.strip() else None

    normalized: Dict[str, Any] = {
        "tenant_id": getattr(request, "tenant_id", None),
        "project_id": getattr(request, "project_id", None),
        "requested_by": getattr(request, "requested_by", None) or getattr(request, "user_id", None),
        "input_type": getattr(request, "input_type", None),
        "content_sha256": content_sha256,
        "source_documents": _normalize_source_documents(getattr(request, "source_documents", None)),
        "options": _normalize_options(getattr(request, "options", None)),
    }
    canonical_json = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_hex(canonical_json)
