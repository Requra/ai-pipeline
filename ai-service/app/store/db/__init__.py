"""
PostgreSQL + pgvector store backend.

Imported lazily by ``app.store.factory`` only when ``DATABASE_URL`` is set, so
the default in-memory path never requires SQLAlchemy's async/asyncpg/pgvector
stack to be installed.
"""

from __future__ import annotations

from typing import Optional

from app.store.base import StoreBundle
from app.store.db.session import Database

_db: Optional[Database] = None


def get_database(url: str) -> Database:
    """Return the process-wide :class:`Database` for ``url`` (one engine)."""
    global _db
    if _db is None or _db.url != Database(url).url:
        _db = Database(url)
    return _db


def build_db_store_bundle(url: str) -> StoreBundle:
    from app.store.db.repositories import (
        PgChunkStore,
        PgEmbeddingStore,
        PgJobStore,
        PgResultStore,
    )

    db = get_database(url)
    return StoreBundle(
        jobs=PgJobStore(db),
        results=PgResultStore(db),
        chunks=PgChunkStore(db),
        embeddings=PgEmbeddingStore(db),
    )
