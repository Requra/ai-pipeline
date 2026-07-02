"""Queue selection + process-wide singleton (Redis when configured, else in-process)."""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.queue.base import QueueClient

_queue: Optional[QueueClient] = None


def get_queue() -> QueueClient:
    global _queue
    if _queue is None:
        if settings.use_redis_queue:
            from app.queue.redis_queue import RedisQueue

            _queue = RedisQueue()
        else:
            from app.queue.inprocess import InProcessQueue

            _queue = InProcessQueue()
    return _queue


def reset_queue() -> None:
    global _queue
    _queue = None
