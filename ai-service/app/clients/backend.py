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

from app.config import settings

logger = logging.getLogger("app.clients.backend")


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
            url = f"{self.base_url}/internal/documents/{document_ref['document_id']}/text"
        if not url:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=self._headers())
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

    async def send_callback(
        self, callback_url: str, payload: Dict[str, Any], *, request_id: Optional[str] = None
    ) -> bool:
        """POST a completion payload to a job callback URL. Best effort."""
        import httpx

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
