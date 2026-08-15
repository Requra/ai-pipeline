"""
Worker process entrypoints.

  * ``run_job_entry(job_id)``       — async: reconstruct state + run one job.
  * ``run_job_entry_sync(job_id)``  — sync wrapper enqueued by RQ.
  * ``main()``                      — start an RQ worker draining the queue.

Run a worker (production):  ``python -m app.worker.main``
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings
from app.startup import run_startup_checks
from app.store.factory import get_stores
from app.worker.runner import execute_job
from app.worker.state import build_worker_initial_state

logger = logging.getLogger("app.worker.main")

# Compile the graph once per worker process (not per job).
_pipeline = None
_REDIS_CONNECT_ATTEMPTS = 10
_REDIS_CONNECT_DELAY_SECONDS = 1.0


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from app.graph.pipeline import build_pipeline

        _pipeline = build_pipeline()
    return _pipeline


async def run_job_entry(job_id: str) -> str:
    """Reconstruct state from the durable store + input cache and run the job."""
    import time
    from app.store.models import JobStatus
    from app.worker.runner import _fail

    stores = get_stores()
    job = await stores.jobs.get_job(job_id)
    if job is None:
        logger.error("run_job_entry: job %s not found", job_id)
        return "FAILED"

    try:
        initial_state = await build_worker_initial_state(job, stores)
    except Exception as exc:
        code = getattr(exc, "code", "SOURCE_RECOVERY_FAILED")
        message = str(exc)
        logger.error("Reconstruction failed for job %s: [%s] %s", job_id, code, message)
        
        # Durable failure update: fail the job, record attempt, write job event
        await _fail(stores, job_id, code, message, time.time())
        
        # Fire callback if configured
        try:
            from app.worker.runner import _maybe_callback
            await _maybe_callback(stores, job, {}, JobStatus.FAILED, request_id=None, backend_client=None)
        except Exception as cb_exc:
            logger.warning("Failed to fire fallback callback for reconstruction error: %s", cb_exc)
            
        return "FAILED"

    return await execute_job(
        stores, job_id, initial_state, _get_pipeline(), use_stream=True
    )


def run_job_entry_sync(job_id: str) -> str:
    """Synchronous entrypoint used by the RQ worker."""
    return asyncio.run(run_job_entry(job_id))


def _connect_redis_with_retry(factory):
    """Wait briefly for Redis DNS/service readiness during container startup."""
    last_error = None
    for attempt in range(1, _REDIS_CONNECT_ATTEMPTS + 1):
        try:
            connection = factory()
            connection.ping()
            return connection
        except Exception as exc:  # pragma: no cover - depends on Docker timing
            last_error = exc
            if attempt < _REDIS_CONNECT_ATTEMPTS:
                logger.warning(
                    "Redis is not ready (attempt %s/%s: %s); retrying.",
                    attempt, _REDIS_CONNECT_ATTEMPTS, type(exc).__name__,
                )
                time.sleep(_REDIS_CONNECT_DELAY_SECONDS)
    raise RuntimeError("Redis was not reachable during worker startup") from last_error


def main() -> None:
    """Start an RQ worker bound to the configured queue."""
    logging.basicConfig(level=logging.INFO)
    if not settings.REDIS_URL:
        raise RuntimeError("REDIS_URL must be set to run the RQ worker.")

    run_startup_checks()

    from rq import Queue, SimpleWorker

    from app.queue.redis_queue import get_redis_connection

    conn = _connect_redis_with_retry(get_redis_connection)
    queue = Queue(settings.QUEUE_NAME, connection=conn)
    worker = SimpleWorker([queue], connection=conn)
    logger.info("AI worker starting — queue=%s", settings.QUEUE_NAME)
    worker.work(with_scheduler=True)


if __name__ == "__main__":  # pragma: no cover
    main()
