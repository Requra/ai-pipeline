"""
Redis-backed production queue (RQ).

Why RQ (over Celery / Dramatiq)?
  * Redis-only — matches our constraint that Redis is used for *queue/dispatch/
    cache only*; no extra broker/result-backend infrastructure.
  * Minimal surface: jobs are coarse-grained (one LangGraph run each) and the
    authoritative state already lives in PostgreSQL, so we only need reliable
    at-least-once *dispatch*, not RQ's result storage.
  * Trivial deployment: a worker is literally ``rq worker <queue>`` (we wrap it
    in ``app.worker.main`` so it shares our config/logging).

Imported lazily by the factory only when ``REDIS_URL`` is set, so ``redis`` / ``rq``
are optional dependencies for the default in-memory path.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger("app.queue.redis_queue")


def get_redis_connection(url: Optional[str] = None):
    """Return a redis client for the configured URL (lazy import)."""
    import redis

    return redis.Redis.from_url(
        url or settings.REDIS_URL,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


class RedisQueue:
    def __init__(self, url: Optional[str] = None, queue_name: Optional[str] = None) -> None:
        self.url = url or settings.REDIS_URL
        self.queue_name = queue_name or settings.QUEUE_NAME
        self._conn = None
        self._queue = None

    @property
    def connection(self):
        if self._conn is None:
            self._conn = get_redis_connection(self.url)
        return self._conn

    @property
    def queue(self):
        if self._queue is None:
            from rq import Queue

            self._queue = Queue(
                self.queue_name,
                connection=self.connection,
                default_timeout=settings.MAX_JOB_RUNTIME_SECONDS,
            )
        return self._queue

    def ping(self) -> bool:
        try:
            return bool(self.connection.ping())
        except Exception as exc:  # pragma: no cover - infra dependent
            logger.warning("redis ping failed: %s", type(exc).__name__)
            return False

    def enqueue(self, job_id: str) -> Optional[str]:
        # Enqueue the sync entrypoint; the worker reconstructs state from the
        # durable store + the Redis input cache and runs the pipeline.
        rq_job = self.queue.enqueue(
            "app.worker.main.run_job_entry_sync",
            job_id,
            job_timeout=settings.MAX_JOB_RUNTIME_SECONDS,
            retry=None,
        )
        return rq_job.id if rq_job else None
