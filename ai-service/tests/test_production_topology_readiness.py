import pytest
from app.config import settings, collect_config_problems
from app.startup import build_readiness_report


@pytest.mark.asyncio
async def test_prod_without_db_not_ready(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "AI_INTERNAL_SERVICE_TOKEN", "secret-token")
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS_RAW", "http://localhost:3000")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")

    problems = collect_config_problems()
    assert any("DATABASE_URL is required in production" in p for p in problems)

    report = await build_readiness_report()
    assert report["ready"] is False
    assert report["checks"]["database"]["ok"] is False


@pytest.mark.asyncio
async def test_prod_without_redis_not_ready(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(settings, "ALLOW_INPROCESS_QUEUE_IN_PRODUCTION", False)
    monkeypatch.setattr(settings, "AI_INTERNAL_SERVICE_TOKEN", "secret-token")
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS_RAW", "http://localhost:3000")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")

    problems = collect_config_problems()
    assert any("REDIS_URL is required in production" in p for p in problems)

    report = await build_readiness_report()
    assert report["ready"] is False
    assert report["checks"]["queue"]["ok"] is False


@pytest.mark.asyncio
async def test_prod_without_redis_allowed_with_explicit_override(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(settings, "ALLOW_INPROCESS_QUEUE_IN_PRODUCTION", True)

    problems = collect_config_problems()
    assert not any("REDIS_URL is required in production" in p for p in problems)


@pytest.mark.asyncio
async def test_dev_environment_permits_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")

    problems = collect_config_problems()
    # In development, missing DB/Redis/auth are not critical blockers
    assert len(problems) == 0
