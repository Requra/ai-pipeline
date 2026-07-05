"""
Worker runner tests: terminal-status mapping, persistence, cancellation, timeout.

Uses fake pipeline objects (an ``ainvoke`` mock and a streaming fake) so no LLM
calls happen and per-node cancellation can be exercised deterministically.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progress import progress_store
from app.store.factory import get_stores, reset_stores
from app.store.models import AiJobRecord, JobOptions, JobStatus
from app.worker.runner import execute_job

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate():
    reset_stores()
    progress_store.clear()
    yield
    reset_stores()
    progress_store.clear()


async def _new_job(job_id="j", **over):
    stores = get_stores()
    rec = AiJobRecord(job_id=job_id, tenant_id="t1", project_id="p1", options=JobOptions(), **over)
    await stores.jobs.create_job(rec)
    return stores


async def test_success_maps_to_completed_and_persists_result():
    stores = await _new_job("ok-1")
    pipe = MagicMock()
    pipe.ainvoke = AsyncMock(return_value={"status": "success", "job_result": {"status": "completed", "job_id": "ok-1"}})
    status = await execute_job(stores, "ok-1", {"chunks": []}, pipe, use_stream=False)
    assert status == JobStatus.COMPLETED.value
    assert (await stores.jobs.get_job("ok-1")).progress_pct == 100
    assert await stores.results.get_result("ok-1") is not None
    # Legacy /status mirror updated too.
    assert progress_store["ok-1"]["status"] == "COMPLETED"


async def test_partial_status_mapping():
    stores = await _new_job("part-1")
    pipe = MagicMock()
    pipe.ainvoke = AsyncMock(return_value={"status": "partial", "job_result": {"status": "partial"}})
    status = await execute_job(stores, "part-1", {}, pipe, use_stream=False)
    assert status == JobStatus.PARTIAL.value
    # Public mirror maps PARTIAL → COMPLETED.
    assert progress_store["part-1"]["status"] == "COMPLETED"


async def test_no_result_is_failure():
    stores = await _new_job("empty-1")
    pipe = MagicMock()
    pipe.ainvoke = AsyncMock(return_value={"status": "success"})  # no job_result
    status = await execute_job(stores, "empty-1", {}, pipe, use_stream=False)
    assert status == JobStatus.FAILED.value
    rec = await stores.jobs.get_job("empty-1")
    assert rec.error_code == "NO_RESULT"


async def test_pipeline_crash_is_failure_not_exception():
    stores = await _new_job("crash-1")
    pipe = MagicMock()
    pipe.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    status = await execute_job(stores, "crash-1", {}, pipe, use_stream=False)
    assert status == JobStatus.FAILED.value
    assert (await stores.jobs.get_job("crash-1")).error_code == "PIPELINE_CRASH"
    attempts = await stores.jobs.list_attempts("crash-1")
    assert len(attempts) == 1
    assert attempts[0].status == JobStatus.FAILED
    assert attempts[0].completed_at is not None


async def test_result_persistence_failure_marks_job_and_attempt_failed():
    stores = await _new_job("persist-fail-1")
    stores.results.save_result = AsyncMock(side_effect=RuntimeError("database unavailable"))
    pipe = MagicMock()
    pipe.ainvoke = AsyncMock(
        return_value={
            "status": "success",
            "job_result": {"status": "completed", "job_id": "persist-fail-1"},
        }
    )

    status = await execute_job(
        stores, "persist-fail-1", {"chunks": []}, pipe, use_stream=False
    )

    assert status == JobStatus.FAILED.value
    job = await stores.jobs.get_job("persist-fail-1")
    assert job.status == JobStatus.FAILED
    assert job.error_code == "PERSISTENCE_ERROR"
    assert progress_store["persist-fail-1"]["status"] == "FAILED"
    attempts = await stores.jobs.list_attempts("persist-fail-1")
    assert len(attempts) == 1
    assert attempts[0].status == JobStatus.FAILED
    assert attempts[0].error_code == "PERSISTENCE_ERROR"


class _StreamGraph:
    def __init__(self, updates):
        self._updates = updates

    async def astream(self, state, stream_mode="updates"):
        for u in self._updates:
            yield u


async def test_stream_path_updates_progress_and_completes():
    stores = await _new_job("stream-1")
    graph = _StreamGraph([
        {"extract": {"extracted_requirements": [1]}},
        {"format": {"status": "completed", "job_result": {"status": "completed"}}},
    ])
    status = await execute_job(stores, "stream-1", {}, graph, use_stream=True)
    assert status == JobStatus.COMPLETED.value


class _LegacyLangGraph026Graph:
    """Mimics langgraph==0.0.26's CompiledGraph.astream — no ``stream_mode``
    parameter at all. Passing it raises the exact error seen in production
    (``Pregel._atransform() got an unexpected keyword argument 'stream_mode'``),
    and the default (no-arg) call yields ``{node: update}`` dicts plus a
    trailing ``{'__end__': <full state>}`` marker.
    """

    def __init__(self, updates):
        self._updates = updates

    async def astream(self, state, **kwargs):
        if "stream_mode" in kwargs:
            raise TypeError(
                "Pregel._atransform() got an unexpected keyword argument 'stream_mode'"
            )
        merged = dict(state)
        for u in self._updates:
            for node_update in u.values():
                if isinstance(node_update, dict):
                    merged.update(node_update)
            yield u
        yield {"__end__": merged}


async def test_stream_path_falls_back_for_legacy_langgraph_without_stream_mode():
    """Regression test for the production crash:

    TypeError: Pregel._atransform() got an unexpected keyword argument
    'stream_mode' — raised by the pinned langgraph==0.0.26, whose astream()
    has no stream_mode parameter. The runner must detect this on the first
    chunk and transparently fall back to the legacy default streaming call
    (which yields the same {node: update} shape), completing the job normally
    instead of failing it.
    """
    stores = await _new_job("legacy-stream-1")
    graph = _LegacyLangGraph026Graph([
        {"extract": {"extracted_requirements": [1]}},
        {"format": {"status": "completed", "job_result": {"status": "completed"}}},
    ])
    status = await execute_job(stores, "legacy-stream-1", {}, graph, use_stream=True)
    assert status == JobStatus.COMPLETED.value
    rec = await stores.jobs.get_job("legacy-stream-1")
    assert rec.error_code is None
    assert await stores.results.get_result("legacy-stream-1") is not None


async def test_cancellation_before_start():
    stores = await _new_job("cancel-early")
    await stores.jobs.request_cancel("cancel-early")
    pipe = MagicMock()
    pipe.ainvoke = AsyncMock(return_value={"status": "success", "job_result": {"status": "completed"}})
    status = await execute_job(stores, "cancel-early", {}, pipe, use_stream=False)
    assert status == JobStatus.CANCELLED.value
    attempts = await stores.jobs.list_attempts("cancel-early")
    assert len(attempts) == 1
    assert attempts[0].status == JobStatus.CANCELLED


async def test_cancellation_between_nodes():
    stores = await _new_job("cancel-mid")
    await stores.jobs.request_cancel("cancel-mid")
    graph = _StreamGraph([
        {"extract": {}},
        {"generate": {}},
        {"format": {"status": "completed", "job_result": {"status": "completed"}}},
    ])
    status = await execute_job(stores, "cancel-mid", {}, graph, use_stream=True)
    assert status == JobStatus.CANCELLED.value


async def test_callback_fired_on_completion():
    stores = await _new_job("cb-1", callback_url="https://backend.example/callbacks/cb-1")
    pipe = MagicMock()
    pipe.ainvoke = AsyncMock(return_value={"status": "success", "job_result": {"status": "completed"}})
    backend = MagicMock()
    backend.send_callback = AsyncMock(return_value=True)
    status = await execute_job(stores, "cb-1", {}, pipe, use_stream=False, backend_client=backend)
    assert status == JobStatus.COMPLETED.value
    backend.send_callback.assert_awaited_once()
    args, kwargs = backend.send_callback.call_args
    assert args[0] == "https://backend.example/callbacks/cb-1"
    assert args[1]["job_id"] == "cb-1"
