"""
build_source_index node.

Turns the parsed ``chunks`` into a retrievable in-memory lexical index for the
job and records lightweight retrieval stats in state. The (non-serializable)
retriever lives in ``app.rag.source_index``; state only carries the
``source_index_id`` handle so it stays JSON-serializable.

Pass-through safe: with no chunks it records an empty index and a warning, and
never blocks the pipeline.
"""

from __future__ import annotations

from typing import List

from app.progress import update_progress
from app.rag.source_index import build_source_index
from app.schemas.items import PipelineWarning, SourceChunk
from app.schemas.pipeline_state import PipelineState


async def build_source_index_node(state: PipelineState) -> dict:
    print("--- BUILD SOURCE INDEX NODE ---")
    job_id = state.get("job_id") or ""
    update_progress(job_id, "build_source_index", 35, "PROCESSING")

    chunks: List[SourceChunk] = state.get("chunks", []) or []

    if not chunks:
        existing_warnings = state.get("warnings", []) or []
        warning = PipelineWarning(
            node_name="build_source_index",
            code="SOURCE_INDEX_EMPTY",
            message="No chunks available to index; evidence retrieval will be skipped.",
        )
        return {
            "source_index_id": None,
            "retrieval_stats": {"indexed_chunks": 0, "vocabulary_size": 0, "avg_chunk_tokens": 0.0},
            "warnings": existing_warnings + [warning],
        }

    stats = build_source_index(job_id, chunks)
    return {
        "source_index_id": job_id,
        "retrieval_stats": stats,
    }
