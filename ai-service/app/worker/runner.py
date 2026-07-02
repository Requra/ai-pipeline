"""
Job runner — the single execution path for every AI job.

Responsibilities (spec §JOB EXECUTION):
  * Load the ai_job, mark PROCESSING, record an attempt.
  * Run the LangGraph pipeline (streaming per-node in the worker path so progress
    and cancellation are observed *between* nodes; single ``ainvoke`` in the
    lightweight in-process path).
  * Persist intermediate + final artifacts (chunks, result, quality) to the
    durable stores.
  * Honour cooperative cancellation and a hard runtime timeout.
  * Map the terminal state to COMPLETED / PARTIAL / REJECTED / FAILED / CANCELLED.
  * Fire the backend callback when a ``callback_url`` is set.

The pipeline object is passed in (never imported here) so:
  * the in-process/demo path can hand in ``app.main.pipeline`` (mock-patchable in
    tests), and
  * the worker process can compile its own graph.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from app.config import settings
from app.progress import update_progress
from app.rag.source_index import clear_source_index
from app.store.base import StoreBundle
from app.store.models import (
    AiJobRecord,
    JobAttemptRecord,
    JobEventRecord,
    JobStatus,
    utcnow,
)
from app.worker.persistence import (
    persist_result,
    persist_source_documents_and_chunks,
    result_to_public,
)

logger = logging.getLogger("app.worker.runner")

# Node → progress percentage, mirrors the values the nodes already emit so the
# durable store and the legacy /status progress stay consistent.
PROGRESS_BY_NODE = {
    "detect_file_type": 5,
    "ingest": 12,
    "transcribe": 20,
    "parse_to_chunks": 30,
    "build_source_index": 35,
    "extract": 45,
    "dedupe_requirements": 55,
    "retrieve_evidence": 62,
    "classify": 70,
    "evidence_grounding": 76,
    "generate": 85,
    "quality_gate": 90,
    "summarize": 95,
    "format": 100,
}

# Terminal pipeline status → (durable JobStatus, public progress status, contract status)
_STATUS_MAP = {
    "completed": (JobStatus.COMPLETED, "COMPLETED", "completed"),
    "success": (JobStatus.COMPLETED, "COMPLETED", "completed"),
    "partial": (JobStatus.PARTIAL, "COMPLETED", "partial"),
    "needs_review": (JobStatus.PARTIAL, "COMPLETED", "partial"),
    "rejected": (JobStatus.REJECTED, "COMPLETED", "rejected"),
    "failed": (JobStatus.FAILED, "FAILED", "failed"),
    "error": (JobStatus.FAILED, "FAILED", "failed"),
}


def _resolve_terminal(final_state: Dict[str, Any], job_result: Dict[str, Any]):
    raw = None
    if isinstance(job_result, dict):
        raw = job_result.get("status")
    raw = raw or final_state.get("status") or "completed"
    return _STATUS_MAP.get(str(raw).lower(), (JobStatus.COMPLETED, "COMPLETED", "completed"))


async def _event(stores: StoreBundle, job_id: str, event_type: str, **kw) -> None:
    try:
        await stores.jobs.add_event(
            JobEventRecord(job_id=job_id, event_type=event_type, **kw)
        )
    except Exception:  # pragma: no cover - store dependent
        pass


async def _iter_pipeline_updates(pipeline: Any, initial_state: Dict[str, Any]):
    """Yield ``{node_name: update}`` dicts across langgraph API versions.

    This project pins ``langgraph==0.0.26`` (see pyproject.toml — the graph's
    ``PIPELINE_RECURSION_LIMIT`` tuning is specific to that version's BSP
    super-step accounting), whose ``CompiledGraph.astream()`` has no
    ``stream_mode`` parameter at all — passing it raises
    ``TypeError: ..._atransform() got an unexpected keyword argument
    'stream_mode'`` deep in Pregel's internals, and does so on the very first
    ``__anext__()``, before any node has executed. Newer langgraph requires
    ``stream_mode="updates"`` to get this same ``{node_name: update}`` shape
    (its default is a full-state "values" stream instead). Try the modern call
    first and fall back to the legacy default call — which already yields the
    same shape — only if the failure occurred before any chunk was produced, so
    no node ever runs twice.
    """
    yielded_any = False
    try:
        async for update in pipeline.astream(initial_state, stream_mode="updates"):
            yielded_any = True
            yield update
        return
    except TypeError as exc:
        if yielded_any or "stream_mode" not in str(exc):
            raise
        logger.info(
            "pipeline.astream() has no stream_mode support (legacy langgraph); "
            "falling back to its default streaming behavior"
        )

    async for update in pipeline.astream(initial_state):
        if isinstance(update, dict) and set(update.keys()) == {"__end__"}:
            # Legacy-only terminal marker carrying the full final state, which
            # is already covered by the last real node's update.
            continue
        yield update


async def _run_stream(
    stores: StoreBundle, job: AiJobRecord, initial_state: Dict[str, Any], pipeline: Any
) -> Optional[Dict[str, Any]]:
    """Stream the pipeline node-by-node; return final state or None if cancelled."""
    final_state: Dict[str, Any] = dict(initial_state)
    async for update in _iter_pipeline_updates(pipeline, initial_state):
        if await stores.jobs.is_cancel_requested(job.job_id):
            return None
        if not isinstance(update, dict):
            continue
        for node_name, node_update in update.items():
            if isinstance(node_update, dict):
                final_state.update(node_update)
            pct = PROGRESS_BY_NODE.get(node_name, job.progress_pct)
            await stores.jobs.set_status(
                job.job_id,
                JobStatus.PROCESSING,
                current_node=node_name,
                progress_pct=pct,
            )
            update_progress(job.job_id, node_name, pct, "PROCESSING")
            await _persist_incremental(stores, job, node_name, final_state)
    return final_state


async def _persist_incremental(
    stores: StoreBundle, job: AiJobRecord, node_name: str, state: Dict[str, Any]
) -> None:
    """Persist major artifacts as their producing node completes."""
    try:
        if node_name in ("parse_to_chunks", "transcribe"):
            await persist_source_documents_and_chunks(stores, job, state)
    except Exception as exc:  # pragma: no cover
        logger.warning("incremental persist failed at %s: %s", node_name, type(exc).__name__)


async def execute_job(
    stores: StoreBundle,
    job_id: str,
    initial_state: Dict[str, Any],
    pipeline: Any,
    *,
    use_stream: bool = True,
    request_id: Optional[str] = None,
    backend_client: Any = None,
) -> str:
    """Run one job to a terminal state. Returns the durable status value."""
    job = await stores.jobs.get_job(job_id)
    if job is None:
        # Defensive: synthesize a minimal record so a mis-sequenced call still runs.
        job = AiJobRecord(job_id=job_id)
        await stores.jobs.create_job(job)

    started = time.time()
    await stores.jobs.add_attempt(
        JobAttemptRecord(job_id=job_id, attempt_number=job.attempt_number)
    )
    await stores.jobs.set_status(
        job_id, JobStatus.PROCESSING, current_node="started", progress_pct=1
    )
    update_progress(job_id, "started", 1, "PROCESSING")
    await _event(stores, job_id, "job_started", message="pipeline execution started")

    # Cancelled before it even started?
    if await stores.jobs.is_cancel_requested(job_id):
        await stores.jobs.set_status(job_id, JobStatus.CANCELLED, current_node="cancelled")
        update_progress(job_id, "cancelled", job.progress_pct, "FAILED",
                         error="JOB_CANCELLED: cancelled before start")
        await _event(stores, job_id, "job_cancelled", severity="warning")
        return JobStatus.CANCELLED.value

    final_state: Optional[Dict[str, Any]] = None
    try:
        runtime = max(1, int(settings.MAX_JOB_RUNTIME_SECONDS))
        if use_stream and hasattr(pipeline, "astream"):
            final_state = await asyncio.wait_for(
                _run_stream(stores, job, initial_state, pipeline), timeout=runtime
            )
        else:
            final_state = await asyncio.wait_for(
                pipeline.ainvoke(initial_state), timeout=runtime
            )
    except asyncio.TimeoutError:
        await _fail(stores, job_id, "JOB_TIMEOUT", "job exceeded max runtime", started)
        return JobStatus.FAILED.value
    except Exception as exc:  # pragma: no cover - pipeline should not crash, but be safe
        err = f"{type(exc).__name__}: {exc}"
        logger.error("job %s crashed: %s", job_id, type(exc).__name__)
        await _fail(stores, job_id, "PIPELINE_CRASH", err, started)
        return JobStatus.FAILED.value
    finally:
        clear_source_index(job_id)

    # Cancelled mid-run (stream returned None).
    if final_state is None:
        await stores.jobs.set_status(job_id, JobStatus.CANCELLED, current_node="cancelled")
        update_progress(job_id, "cancelled", job.progress_pct, "FAILED",
                         error="JOB_CANCELLED: cancelled during processing")
        await _event(stores, job_id, "job_cancelled", severity="warning")
        return JobStatus.CANCELLED.value

    job_result_obj = final_state.get("job_result")
    job_result = result_to_public(job_result_obj)
    durable, public_status, contract_status = _resolve_terminal(final_state, job_result)

    if not job_result and durable != JobStatus.FAILED:
        # Pipeline finished but produced no result payload — treat as failure.
        await _fail(stores, job_id, "NO_RESULT", "pipeline produced no job_result", started)
        return JobStatus.FAILED.value

    processing_time_ms = int(max(0, (time.time() - started) * 1000))

    # Persist chunks (end-of-run for the in-process/ainvoke path) + final result.
    try:
        if not use_stream:
            await persist_source_documents_and_chunks(stores, job, final_state)
        job_result = await persist_result(
            stores, job, job_result_obj,
            contract_status=contract_status, processing_time_ms=processing_time_ms,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("final persist failed for %s: %s", job_id, type(exc).__name__)

    err_msg = final_state.get("error") if durable == JobStatus.FAILED else None
    await stores.jobs.set_status(
        job_id, durable, current_node="format", progress_pct=100,
        error_message=err_msg,
    )
    # Mirror to the legacy in-memory /status store (keeps existing clients/tests working).
    update_progress(
        job_id, "format", 100, public_status,
        result=job_result if public_status == "COMPLETED" else None,
        error=err_msg,
    )
    await stores.jobs.add_attempt(
        JobAttemptRecord(
            job_id=job_id, attempt_number=job.attempt_number, status=durable,
            completed_at=utcnow(),
        )
    )
    await _event(
        stores, job_id, "job_finished",
        message=f"terminal status={durable.value}", node_name="format",
        metadata={"processing_time_ms": processing_time_ms},
    )

    await _maybe_callback(stores, job, job_result, durable, request_id, backend_client)
    return durable.value


async def _fail(stores: StoreBundle, job_id: str, code: str, message: str, started: float) -> None:
    await stores.jobs.set_status(
        job_id, JobStatus.FAILED, current_node="failed", progress_pct=100,
        error_code=code, error_message=message,
    )
    update_progress(job_id, "failed", 100, "FAILED", error=f"{code}: {message}")
    await _event(stores, job_id, "job_failed", severity="error",
                 message=message, metadata={"error_code": code})


async def _maybe_callback(
    stores: StoreBundle, job: AiJobRecord, result: Dict[str, Any],
    status: JobStatus, request_id: Optional[str], backend_client: Any,
) -> None:
    callback_url = job.callback_url or job.options.callback_url
    if not callback_url:
        return
    if backend_client is None:
        from app.clients.backend import BackendDocumentClient
        backend_client = BackendDocumentClient()
    payload = {
        "job_id": job.job_id,
        "tenant_id": job.tenant_id,
        "project_id": job.project_id,
        "status": status.value,
        "result": result,
    }
    ok = await backend_client.send_callback(callback_url, payload, request_id=request_id)
    await _event(
        stores, job.job_id, "callback_sent" if ok else "callback_failed",
        severity="info" if ok else "warning",
        message=f"callback to {callback_url[:120]}",
    )
