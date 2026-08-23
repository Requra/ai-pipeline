"""Queue topology regression tests.

An API-local in-process queue cannot replace Redis/RQ while execution lives in
a separate worker service.  These tests protect the no-stranded-QUEUED-jobs
behavior for the production topology.
"""

import pytest

from app.config import settings
from app.queue import factory
from app.queue.factory import QueueUnavailableError
from app.startup import _probe_redis
from app.store.factory import get_stores, reset_stores
from app.store.models import AiJobRecord, JobStatus
from app.worker.dispatch import JobDispatchError, dispatch_job


@pytest.fixture(autouse=True)
def reset_queue():
    factory.reset_queue()
    reset_stores()
    yield
    factory.reset_queue()
    reset_stores()


def test_production_redis_failure_does_not_fallback_to_api_local_queue(monkeypatch):
    class UnreachableRedisQueue:
        def ping(self):
            return False

    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://redis.invalid:6379/0")
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue", UnreachableRedisQueue)

    with pytest.raises(QueueUnavailableError):
        factory.get_queue()


@pytest.mark.asyncio
async def test_production_readiness_fails_when_configured_redis_is_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://redis.invalid:6379/0")
    monkeypatch.setattr(
        "app.queue.redis_queue.get_redis_connection",
        lambda _url: type("BrokenRedis", (), {"ping": lambda self: (_ for _ in ()).throw(OSError())})(),
    )

    check = _probe_redis()

    assert check["ok"] is False
    assert check["backend"] == "redis"


@pytest.mark.asyncio
async def test_dispatch_failure_marks_previously_accepted_job_failed(monkeypatch):
    class RejectingRedisQueue:
        def __init__(self):
            pass

        def enqueue(self, _job_id):
            raise OSError("redis connection lost")

    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://redis.invalid:6379/0")
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue", RejectingRedisQueue)

    stores = get_stores()
    await stores.jobs.create_job(AiJobRecord(job_id="dispatch-failure"))

    with pytest.raises(JobDispatchError, match="QUEUE_DISPATCH_FAILED"):
        await dispatch_job("dispatch-failure")

    job = await stores.jobs.get_job("dispatch-failure")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error_code == "QUEUE_DISPATCH_FAILED"
