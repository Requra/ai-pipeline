"""
Backend integration client.

The .NET backend owns raw files/audio and their storage. This client is how the
AI service:

  * fetches already-extracted/transcribed *text* (or a document reference) for a
    job when the backend did not inline it, and
  * pushes the final result back via a job ``callback_url``.

It never downloads or persists original file bytes — the AI service does not own
raw file storage. All calls are best-effort and time-bounded; a callback failure
is logged (safely) and recorded as a job event, it does not fail the job.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger("app.clients.backend")


# ── Typed Download Exceptions ──────────────────────────────────────────────────


class SourceDownloadError(Exception):
    code: str


class SourceUnavailableError(SourceDownloadError):
    code = "SOURCE_INPUT_UNAVAILABLE"


class SourceIntegrityError(SourceDownloadError):
    code = "SOURCE_HASH_MISMATCH"


class SourceTooLargeError(SourceDownloadError):
    code = "SOURCE_TOO_LARGE"


class SourceSecurityError(SourceDownloadError):
    code = "SOURCE_URL_REJECTED"


# ── URL Helpers ───────────────────────────────────────────────────────────────


def _is_safe_ip(ip_str: str) -> bool:
    import ipaddress

    try:
        ip = ipaddress.ip_address(ip_str)
        return not (
            ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
        )
    except ValueError:
        return False


def _validate_host_safety(host: str, is_backend: bool) -> None:
    import socket

    if not host:
        raise SourceSecurityError("Invalid host")

    if is_backend:
        return

    try:
        addr_info = socket.getaddrinfo(host, None)
        for item in addr_info:
            ip = item[4][0]
            # Strip scope id if IPv6
            if "%" in ip:
                ip = ip.split("%")[0]
            if not _is_safe_ip(ip):
                raise SourceSecurityError("Unsafe destination address resolved")
    except socket.gaierror:
        raise SourceSecurityError("DNS resolution failed for source host")


def _is_approved_host(host: str, base_url: str) -> bool:
    import urllib.parse
    import os

    if base_url:
        parsed_base = urllib.parse.urlparse(base_url)
        if parsed_base.hostname and host.lower() == parsed_base.hostname.lower():
            return True

    allowed_raw = os.environ.get("ALLOWED_DOWNLOAD_DOMAINS", "")
    allowed_domains = [d.strip().lower() for d in allowed_raw.split(",") if d.strip()]
    if not allowed_domains:
        allowed_domains = [
            "s3.amazonaws.com",
            "res.cloudinary.com",
            "storage.googleapis.com",
            "cloudfront.net",
        ]

    host_lower = host.lower()
    for domain in allowed_domains:
        if host_lower == domain or host_lower.endswith("." + domain):
            return True

    return False


def _is_backend_origin(url: str, base_url: str) -> bool:
    if not url or not base_url:
        return False
    parsed = urlparse(url)
    parsed_base = urlparse(base_url)
    return (
        parsed.scheme in ("http", "https")
        and parsed_base.scheme in ("http", "https")
        and parsed.hostname is not None
        and parsed_base.hostname is not None
        and parsed.hostname.lower() == parsed_base.hostname.lower()
        and (parsed.port or _default_port(parsed.scheme))
        == (parsed_base.port or _default_port(parsed_base.scheme))
    )


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _redacted_url_for_log(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


class BackendDocumentClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        service_token: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or settings.BACKEND_BASE_URL or "").rstrip("/")
        self.service_token = service_token or settings.BACKEND_SERVICE_TOKEN
        self.timeout = timeout_seconds or settings.CALLBACK_TIMEOUT_SECONDS

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
        return headers

    async def fetch_document_text(self, document_ref: Dict[str, Any]) -> Optional[str]:
        """Fetch extracted/transcribed text for a backend document reference.

        Prefers a ``file_url`` when the backend provides one; otherwise calls
        ``GET {BACKEND_BASE_URL}/internal/documents/{document_id}/text``. Returns
        ``None`` when no source is configured (caller then degrades gracefully).
        """
        import httpx

        url: Optional[str] = document_ref.get("file_url")
        if not url and self.base_url and document_ref.get("document_id"):
            url = (
                f"{self.base_url}/internal/documents/{document_ref['document_id']}/text"
            )
        if not url:
            return None

        try:
            parsed = urlparse(url)
            if parsed.username or parsed.password:
                raise SourceSecurityError("User-info URLs are not allowed")
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise SourceSecurityError("Only valid HTTP/HTTPS URLs are allowed")
            is_backend = _is_backend_origin(url, self.base_url)
            if not _is_approved_host(parsed.hostname, self.base_url):
                raise SourceSecurityError("Host is not allowlisted for text fetch")
            _validate_host_safety(parsed.hostname, is_backend)
            headers = {"Accept": "application/json, text/plain;q=0.9"}
            if is_backend and self.service_token:
                headers["Authorization"] = f"Bearer {self.service_token}"
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=headers, follow_redirects=False)
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if "application/json" in ctype:
                    data = resp.json()
                    if isinstance(data, dict):
                        return data.get("text") or data.get("content")
                    return None
                return resp.text
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning(
                "backend document fetch failed for document_id=%s: %s",
                document_ref.get("document_id"),
                type(exc).__name__,
            )
            return None

    async def fetch_document_bytes(self, document_ref: Dict[str, Any]) -> bytes:
        """Download raw binary document/audio bytes safely.

        Applies security checks (SSRF, redirects, no credential leak, domain check, size limit, hash check).
        """
        import httpx
        import urllib.parse
        import hashlib

        url: Optional[str] = document_ref.get("file_url")
        doc_id = document_ref.get("document_id")

        # If no url is supplied, default to the backend document endpoint
        if not url and self.base_url and doc_id:
            url = f"{self.base_url}/internal/documents/{doc_id}/content"

        if not url:
            raise SourceUnavailableError("No URL or document ID provided for retrieval")

        # 1. Parse and validate host safety
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SourceSecurityError("Only HTTP and HTTPS schemes are allowed")
        if parsed.username or parsed.password:
            raise SourceSecurityError("User-info URLs are not allowed")

        hostname = parsed.hostname
        if not hostname:
            raise SourceSecurityError("Invalid URL hostname")

        # Resolve is_backend flag
        is_backend = False
        if self.base_url:
            parsed_base = urllib.parse.urlparse(self.base_url)
            if parsed.netloc.lower() == parsed_base.netloc.lower():
                is_backend = True

        # Check approved domain
        if not _is_approved_host(hostname, self.base_url):
            raise SourceSecurityError("Host is not allowlisted for downloads")

        # SSRF checks
        _validate_host_safety(hostname, is_backend)

        # Set up headers
        headers = {"Accept": "*/*"}
        if is_backend and self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"

        # Bounded limits based on file type
        file_type = document_ref.get("file_type") or "document"
        max_bytes = 50 * 1024 * 1024 if file_type == "audio" else 20 * 1024 * 1024

        # Download safely in chunks without logging credentials/URL query parameters
        safe_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
        )
        logger.info("fetch_document_bytes starting download: %s", safe_url)

        downloaded = bytearray()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Enforce manual redirect handling for SSRF/token leak prevention
                async with client.stream(
                    "GET", url, headers=headers, follow_redirects=False
                ) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        redirect_location = resp.headers.get("location")
                        if not redirect_location:
                            raise SourceUnavailableError(
                                "Redirect has no location header"
                            )

                        redirect_url = urllib.parse.urljoin(url, redirect_location)
                        parsed_redir = urllib.parse.urlparse(redirect_url)
                        if parsed_redir.scheme not in ("http", "https"):
                            raise SourceSecurityError(
                                "Only HTTP/HTTPS redirect URLs are allowed"
                            )

                        redir_hostname = parsed_redir.hostname
                        if not redir_hostname:
                            raise SourceSecurityError("Invalid redirect URL hostname")

                        # Recheck redirects safety
                        redir_is_backend = False
                        if self.base_url:
                            if (
                                parsed_redir.netloc.lower()
                                == parsed_base.netloc.lower()
                            ):
                                redir_is_backend = True

                        if not _is_approved_host(redir_hostname, self.base_url):
                            raise SourceSecurityError(
                                "Redirect host is not allowlisted"
                            )

                        _validate_host_safety(redir_hostname, redir_is_backend)

                        redir_headers = {"Accept": "*/*"}
                        if redir_is_backend and self.service_token:
                            redir_headers["Authorization"] = (
                                f"Bearer {self.service_token}"
                            )

                        async with client.stream(
                            "GET",
                            redirect_url,
                            headers=redir_headers,
                            follow_redirects=False,
                        ) as resp2:
                            resp2.raise_for_status()
                            async for chunk in resp2.aiter_bytes():
                                downloaded.extend(chunk)
                                if len(downloaded) > max_bytes:
                                    raise SourceTooLargeError(
                                        "Source file exceeds allowable size limit"
                                    )
                    else:
                        resp.raise_for_status()
                        async for chunk in resp.aiter_bytes():
                            downloaded.extend(chunk)
                            if len(downloaded) > max_bytes:
                                raise SourceTooLargeError(
                                    "Source file exceeds allowable size limit"
                                )
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 410):
                raise SourceUnavailableError(
                    f"Source file not found (status {e.response.status_code})"
                )
            raise SourceUnavailableError(
                f"Source download HTTP error: {e.response.status_code}"
            )
        except httpx.RequestError as e:
            raise SourceUnavailableError(
                f"Network request error occurred: {type(e).__name__}"
            )

        raw_bytes = bytes(downloaded)

        # Integrity check
        expected_hash = document_ref.get("sha256_hash") or document_ref.get("hash")
        if expected_hash:
            actual_hash = hashlib.sha256(raw_bytes).hexdigest()
            if actual_hash.lower() != expected_hash.lower():
                raise SourceIntegrityError(
                    "Checksum verification failed for downloaded content"
                )

        return raw_bytes

    async def send_callback(
        self,
        callback_url: str,
        payload: Dict[str, Any],
        *,
        request_id: Optional[str] = None,
    ) -> bool:
        """POST a completion payload to a job callback URL. Best effort."""
        import httpx
        import urllib.parse

        if not _is_backend_origin(callback_url, self.base_url):
            logger.warning(
                "callback URL rejected for job_id=%s: callback origin is not configured backend origin",
                payload.get("job_id"),
            )
            return False

        headers = self._headers()
        headers["Content-Type"] = "application/json"
        if request_id:
            headers["X-Request-Id"] = request_id
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(callback_url, json=payload, headers=headers)
                resp.raise_for_status()
                return True
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning(
                "callback POST failed for job_id=%s: %s",
                payload.get("job_id"),
                type(exc).__name__,
            )
            return False
