"""
Queue client interface.

``enqueue(job_id)`` is the production dispatch contract: hand a job id to a
worker that reconstructs state from the durable store + Redis input cache. The
in-process implementation additionally supports ``submit_inprocess`` for the
lightweight single-node path where the state object is already in memory.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class QueueClient(Protocol):
    def enqueue(self, job_id: str) -> Optional[str]:
        """Dispatch a job by id to a worker. Returns an optional queue message id."""
        ...

    def ping(self) -> bool:
        """Return True when the queue backend is reachable."""
        ...
