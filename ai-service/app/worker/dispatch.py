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
            import logging
            logger = logging.getLogger("app.worker.dispatch")
            nonlocal initial_state
            
            is_reference_job = (
                initial_state is None
                or (
                    not initial_state.get("raw_bytes")
                    and not initial_state.get("raw_text")
                    and not initial_state.get("raw_inputs")
                )
            )
            
            if is_reference_job:
                try:
                    from app.worker.state import build_worker_initial_state
                    job = await stores.jobs.get_job(job_id)
                    if job:
                        initial_state = await build_worker_initial_state(job, stores)
                except Exception as exc:
                    logger.error("In-process background state reconstruction failed for %s: %s", job_id, exc)
                    
                    import time
                    from app.worker.runner import _fail, _maybe_callback
                    from app.store.models import JobStatus
                    
                    code = getattr(exc, "code", "SOURCE_RECOVERY_FAILED")
                    message = str(exc)
                    await _fail(stores, job_id, code, message, time.time())
                    
                    job = await stores.jobs.get_job(job_id)
                    if job:
                        try:
                            await _maybe_callback(stores, job, {}, JobStatus.FAILED, request_id=request_id, backend_client=None)
                        except Exception as cb_exc:
                            logger.warning("Failed to fire callback for in-process reconstruction error: %s", cb_exc)
                    return

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
        try:
            stash_input(job_id, **cache_input)
        except Exception as exc:
            raise RuntimeError(f"INPUT_CACHE_FAILED: {exc}") from exc

    try:
        queue.enqueue(job_id)
    except Exception as exc:
        raise RuntimeError(f"QUEUE_DISPATCH_FAILED: {exc}") from exc
