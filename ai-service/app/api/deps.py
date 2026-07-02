"""
Shared API dependencies: service-to-service auth + request tracing.

Auth policy for ``/internal/*`` (spec §AUTH & SECURITY):
  * A caller must present ``Authorization: Bearer <AI_INTERNAL_SERVICE_TOKEN>``.
  * Missing/malformed header → 401; wrong token → 403 (constant-time compare).
  * If the token is not configured at all: rejected in production (should never
    happen — startup fails fast), allowed in non-production for local dev.
"""

from __future__ import annotations

import logging
import secrets
import uuid

from fastapi import Header, HTTPException, Request, status
from typing import Optional

from app.config import settings

logger = logging.getLogger("app.api")

REQUEST_ID_HEADER = "X-Request-Id"


def get_request_id(request: Request) -> str:
    """Return the per-request id set by the tracing middleware (or generate one)."""
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = uuid.uuid4().hex
        request.state.request_id = rid
    return rid


async def require_internal_auth(
    authorization: Optional[str] = Header(default=None),
) -> None:
    """FastAPI dependency guarding internal endpoints."""
    configured = settings.AI_INTERNAL_SERVICE_TOKEN

    if not configured:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="internal service auth is not configured",
            )
        # Dev convenience: no token configured → allow, but make it visible.
        logger.warning("AI_INTERNAL_SERVICE_TOKEN unset — /internal/* is UNAUTHENTICATED (dev only)")
        return

    if not authorization or not authorization.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(token.strip(), configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid service token",
        )
