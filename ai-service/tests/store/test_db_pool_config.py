import pytest
from app.config import settings
from app.store.db.session import Database


def test_database_engine_uses_configured_pool_settings(monkeypatch):
    monkeypatch.setattr(settings, "DB_POOL_SIZE", 8)
    monkeypatch.setattr(settings, "DB_MAX_OVERFLOW", 12)
    monkeypatch.setattr(settings, "DB_POOL_TIMEOUT_SECONDS", 45)
    monkeypatch.setattr(settings, "DB_POOL_RECYCLE_SECONDS", 900)

    db = Database("postgresql+asyncpg://test:test@localhost:5432/testdb")
    engine = db.engine

    pool = engine.pool
    assert pool.size() == 8
    assert pool._max_overflow == 12
    assert pool._timeout == 45
    assert pool._recycle == 900
