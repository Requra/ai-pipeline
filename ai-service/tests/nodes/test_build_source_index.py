"""Phase 2 — build_source_index node + per-job registry + graph wiring."""

from __future__ import annotations

import pytest

from app.nodes.build_source_index import build_source_index_node
from app.rag.source_index import (
    build_source_index,
    clear_source_index,
    get_source_index,
    source_index_size,
)
from app.schemas.items import SourceChunk


def _chunk(cid: str, text: str, idx: int = 0) -> SourceChunk:
    return SourceChunk(chunk_id=cid, text=text, start_char=idx, end_char=idx + len(text))


def _state(job_id: str, chunks):
    return {"job_id": job_id, "chunks": chunks, "warnings": []}


@pytest.mark.asyncio
async def test_node_indexes_chunks_and_records_stats():
    chunks = [
        _chunk("c0", "The system must export invoices to PDF.", 0),
        _chunk("c1", "Users reset passwords via a verification email.", 50),
    ]
    job_id = "idx-job-1"
    clear_source_index(job_id)

    out = await build_source_index_node(_state(job_id, chunks))

    assert out["source_index_id"] == job_id
    assert out["retrieval_stats"]["indexed_chunks"] == 2
    assert out["retrieval_stats"]["vocabulary_size"] > 0

    # The retriever is registered and queryable.
    retriever = get_source_index(job_id)
    assert retriever is not None
    hits = retriever.retrieve("export invoices PDF", top_k=1)
    assert hits and hits[0].chunk_id == "c0"
    clear_source_index(job_id)


@pytest.mark.asyncio
async def test_node_handles_empty_chunks_safely():
    out = await build_source_index_node(_state("idx-empty", []))
    assert out["source_index_id"] is None
    assert out["retrieval_stats"]["indexed_chunks"] == 0
    # A warning is recorded but the pipeline is not blocked.
    assert any(w.code == "SOURCE_INDEX_EMPTY" for w in out["warnings"])


def test_registry_build_and_clear():
    job_id = "registry-job"
    before = source_index_size()
    build_source_index(job_id, [_chunk("c0", "requirement about data retention", 0)])
    assert get_source_index(job_id) is not None
    clear_source_index(job_id)
    assert get_source_index(job_id) is None
    # Net registry size unchanged after clear.
    assert source_index_size() == before


def test_pipeline_compiles_with_index_node():
    # Importing/compiling must succeed with the new node wired in.
    from app.graph.pipeline import build_pipeline

    app = build_pipeline()
    assert app is not None
    # The node is present in the compiled graph.
    assert "build_source_index" in app.get_graph().nodes
