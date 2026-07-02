"""Job queue abstraction (in-process default, RQ/Redis for production)."""

from app.queue.base import QueueClient
from app.queue.factory import get_queue, reset_queue

__all__ = ["QueueClient", "get_queue", "reset_queue"]
