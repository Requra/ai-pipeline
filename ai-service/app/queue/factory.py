"""Queue selection + process-wide singleton (Redis when configured, else in-process)."""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.queue.base import QueueClient

_queue: Optional[QueueClient] = None


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
                    if rq.ping():
                        _queue = rq
                        logger.info("Successfully connected to Redis queue.")
                    else:
                        from app.queue.inprocess import InProcessQueue
                        logger.warning("Redis queue is configured but connection ping failed. Falling back to in-process queue!")
                        _queue = InProcessQueue()
                except Exception as exc:
                    from app.queue.inprocess import InProcessQueue
                    logger.warning(
                        "Failed to connect to Redis queue (%s: %s). Falling back to in-process queue!",
                        type(exc).__name__,
                        exc
                    )
                    _queue = InProcessQueue()
            else:
                _queue = rq
        else:
            from app.queue.inprocess import InProcessQueue

            _queue = InProcessQueue()
    return _queue



def reset_queue() -> None:
    global _queue
    _queue = None

