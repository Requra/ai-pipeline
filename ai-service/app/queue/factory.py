"""Queue selection + process-wide singleton (Redis when configured, else in-process)."""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.queue.base import QueueClient

_queue: Optional[QueueClient] = None


class QueueUnavailableError(RuntimeError):
    """Raised when the configured durable queue cannot accept jobs.

    Falling back to an API-local queue while a separate RQ worker is deployed
    strands jobs in the API process.  Callers must surface this as a failed
    dispatch instead of acknowledging a job that no worker can consume.
    """


def get_queue() -> QueueClient:
    global _queue
    from app.queue.redis_queue import RedisQueue
    if _queue is not None:
        if settings.use_redis_queue != isinstance(_queue, RedisQueue):
            _queue = None

    if _queue is None:
        if settings.use_redis_queue:
            import logging
            logger = logging.getLogger("app.queue.factory")

            rq = RedisQueue()

            if settings.is_production:
                try:
                    if not rq.ping():
                        raise QueueUnavailableError("Redis queue ping returned false")
                except Exception as exc:
                    logger.error(
                        "Configured Redis queue is unavailable (%s); refusing API-local fallback.",
                        type(exc).__name__,
                    )
                    raise QueueUnavailableError(
                        "Configured Redis queue is unavailable; job was not enqueued."
                    ) from exc
                _queue = rq
                logger.info("Successfully connected to Redis queue.")
            else:
                _queue = rq
        else:
            from app.queue.inprocess import InProcessQueue

            _queue = InProcessQueue()
    return _queue



def reset_queue() -> None:
    global _queue
    _queue = None

