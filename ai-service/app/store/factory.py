"""
Store selection + process-wide singletons.

``get_stores()`` returns a :class:`StoreBundle` chosen by configuration:

  * ``DATABASE_URL`` set  → PostgreSQL + pgvector backend (``app.store.db``).
  * otherwise             → in-memory backend (dev/demo/tests).

The bundle is a process-wide singleton so the API layer and the in-process queue
share one view of state. The PostgreSQL backend is imported lazily so the default
in-memory path never requires asyncpg/pgvector to be installed.
"""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.store.base import StoreBundle
from app.store.memory import (
    MemoryChunkStore,
    MemoryEmbeddingStore,
    MemoryJobStore,
    MemoryResultStore,
)

_bundle: Optional[StoreBundle] = None


def _build_memory_bundle() -> StoreBundle:
    return StoreBundle(
        jobs=MemoryJobStore(),
        results=MemoryResultStore(),
        chunks=MemoryChunkStore(),
        embeddings=MemoryEmbeddingStore(),
    )


def _build_db_bundle() -> StoreBundle:
    # Lazy import: only pulled in when a database is actually configured, so the
    # default path has no hard dependency on SQLAlchemy async / asyncpg / pgvector.
    from app.store.db import build_db_store_bundle

    return build_db_store_bundle(settings.DATABASE_URL)


def get_stores() -> StoreBundle:
    global _bundle
    if _bundle is None:
        _bundle = _build_db_bundle() if settings.use_database else _build_memory_bundle()
    return _bundle


def reset_stores() -> None:
    """Drop the cached bundle (used by tests to isolate state)."""
    global _bundle
    _bundle = None


async def close_stores() -> None:
    """Release process-owned database resources during graceful shutdown."""
    global _bundle
    if settings.use_database:
        from app.store.db import close_database

        await close_database()
    _bundle = None
