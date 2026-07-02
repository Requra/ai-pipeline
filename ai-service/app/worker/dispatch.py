"""
Job dispatch — the single seam the API uses to run a created job.

Hides the in-process-vs-Redis decision behind one call so endpoints never branch
on queue type:

  * In-process (no REDIS_URL): run ``execute_job`` with the in-memory state via
    the queue's BackgroundTasks-backed submitter. The pipeline object is captured
    from the caller (the demo path passes ``app.main.pipeline``, which stays
    mock-patchable in tests).
  * Redis: cache the transient input, then ``enqueue(job_id)`` so a separate
    worker process reconstructs state and runs it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.config import settings
from app.queue.factory import get_queue
from app.store.factory import get_stores
from app.worker.runner import execute_job


async def dispatch_job(
    job_id: str,
    *,
    initial_state: Optional[Dict[str, Any]] = None,
    pipeline: Any = None,
    background_tasks: Any = None,
    request_id: Optional[str] = None,
    cache_input: Optional[Dict[str, Any]] = None,
) -> None:
    queue = get_queue()

    if not settings.use_redis_queue:
        stores = get_stores()

        async def _factory() -> None:
            await execute_job(
                stores,
                job_id,
                initial_state or {},
                pipeline,
                use_stream=False,
                request_id=request_id,
            )

        # InProcessQueue exposes submit_inprocess (duck-typed here).
        queue.submit_inprocess(_factory, background_tasks=background_tasks)  # type: ignore[attr-defined]
        return

    # Redis path: stash transient input for the worker, then dispatch by id.
    if cache_input is not None:
        from app.worker.state import stash_input

        stash_input(job_id, **cache_input)
    queue.enqueue(job_id)
