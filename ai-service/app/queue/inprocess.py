"""
In-process queue (default; dev/demo/tests).

Runs jobs inside the API process. Two entry styles:

  * ``submit_inprocess`` — used by the demo endpoints, which already hold the
    in-memory pipeline state and want the fast path. Executes via FastAPI
    ``BackgroundTasks`` when available (so the request returns 202 immediately and
    TestClient runs the task synchronously), else via an asyncio task.
  * ``enqueue(job_id)`` — the production contract; reconstructs state from the
    store and runs it. Bounded by ``MAX_CONCURRENT_JOBS`` via a semaphore.

Not durable and not multi-process — production must configure Redis so a separate
worker fleet drains the queue.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from app.config import settings

logger = logging.getLogger("app.queue.inprocess")


class InProcessQueue:
    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(max(1, settings.MAX_CONCURRENT_JOBS))

    def ping(self) -> bool:
        return True

    def submit_inprocess(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        background_tasks: Any = None,
    ) -> None:
        """Schedule an async job callable.

        Prefers FastAPI BackgroundTasks (synchronous under TestClient, so the
        existing async-polling tests keep working); otherwise schedules an
        asyncio task on the running loop.
        """
        async def _guarded() -> None:
            async with self._semaphore:
                try:
                    await coro_factory()
                except Exception:  # pragma: no cover - runner already guards
                    logger.exception("in-process job failed")

        if background_tasks is not None:
            background_tasks.add_task(_guarded)
            return
        try:
            asyncio.get_running_loop().create_task(_guarded())
        except RuntimeError:
            # No running loop (sync context) — run to completion synchronously.
            asyncio.run(_guarded())

    def enqueue(self, job_id: str) -> Optional[str]:
        """Production-style dispatch: reconstruct state from the store and run."""
        from app.worker.main import run_job_entry

        # Run in a background asyncio task if a loop is available, else block.
        async def _factory() -> None:
            await run_job_entry(job_id)

        self.submit_inprocess(_factory)
        return None
