"""
Async SQLAlchemy engine/session management for the PostgreSQL backend.

Lazily constructed — nothing here runs unless a ``DATABASE_URL`` is configured.
The URL is normalised to the asyncpg driver so operators can supply either a
plain ``postgresql://`` DSN or an explicit ``postgresql+asyncpg://`` one.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def normalize_async_url(url: str) -> str:
    """Return a DSN that uses the asyncpg driver."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


class Database:
    """Owns the async engine + session factory for one DSN."""

    def __init__(self, url: str) -> None:
        self.url = normalize_async_url(url)
        self._engine: Optional[AsyncEngine] = None
        self._sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self.url,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=5,
                future=True,
            )
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            self._sessionmaker = async_sessionmaker(
                bind=self.engine, expire_on_commit=False, class_=AsyncSession
            )
        return self._sessionmaker

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def create_all(self) -> None:
        """Create tables + pgvector extension (dev convenience; prod uses Alembic)."""
        from sqlalchemy import text

        from app.store.db.models import Base

        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    async def ping(self) -> bool:
        from sqlalchemy import text

        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
