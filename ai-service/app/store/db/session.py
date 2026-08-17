"""
Async SQLAlchemy engine/session management for the PostgreSQL backend.

Lazily constructed — nothing here runs unless a ``DATABASE_URL`` is configured.
The URL is normalised to the asyncpg driver so operators can supply either a
plain ``postgresql://`` DSN or an explicit ``postgresql+asyncpg://`` one.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

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
    """Owns the async engine + session factory for one DSN, scoped per event loop."""

    def __init__(self, url: str) -> None:
        self.url = normalize_async_url(url)
        self._engines: Dict[Optional[asyncio.AbstractEventLoop], AsyncEngine] = {}
        self._sessionmakers: Dict[Optional[asyncio.AbstractEventLoop], async_sessionmaker[AsyncSession]] = {}

    def _get_current_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    @property
    def engine(self) -> AsyncEngine:
        loop = self._get_current_loop()
        if loop not in self._engines:
            from app.config import settings

            connect_args = {}
            if "ssl=require" in self.url or "sslmode=require" in self.url or "neon.tech" in self.url:
                connect_args["ssl"] = "require"
            self._engines[loop] = create_async_engine(
                self.url,
                pool_pre_ping=True,
                pool_size=getattr(settings, "DB_POOL_SIZE", 5),
                max_overflow=getattr(settings, "DB_MAX_OVERFLOW", 10),
                pool_timeout=getattr(settings, "DB_POOL_TIMEOUT_SECONDS", 30),
                pool_recycle=getattr(settings, "DB_POOL_RECYCLE_SECONDS", 1800),
                future=True,
                connect_args=connect_args,
            )
        return self._engines[loop]

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        loop = self._get_current_loop()
        if loop not in self._sessionmakers:
            self._sessionmakers[loop] = async_sessionmaker(
                bind=self.engine, expire_on_commit=False, class_=AsyncSession
            )
        return self._sessionmakers[loop]

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
        for engine in list(self._engines.values()):
            try:
                await engine.dispose()
            except Exception:
                pass
        self._engines.clear()
        self._sessionmakers.clear()
