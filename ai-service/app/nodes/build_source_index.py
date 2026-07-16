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

import logging
from typing import List

from app.progress import update_progress
from app.rag.source_index import build_source_index
from app.schemas.items import PipelineWarning, SourceChunk
from app.schemas.pipeline_state import PipelineState

logger = logging.getLogger("app.nodes.build_source_index")


async def _maybe_generate_embeddings(state: PipelineState, chunks: List[SourceChunk]) -> dict:
    """Generate + persist chunk embeddings when the job opted in.

    Fully opt-in and defensive: if embeddings are disabled, no provider is
    configured, or persistence fails, this is a silent no-op (BM25 grounding is
    unaffected). Embeddings are scoped by tenant/project/job so semantic recall
    never crosses tenant or project boundaries.
    """
    if not state.get("enable_embeddings"):
        return {}
    try:
        from app.rag.embeddings import get_embedder
        from app.store.factory import get_stores
        from app.store.models import ChunkEmbeddingRecord

        embedder = get_embedder()
        if embedder is None:
            return {"embeddings_indexed": 0, "embeddings_status": "no_provider"}

        vectors = await embedder.embed_documents([c.text for c in chunks])
        records = [
            ChunkEmbeddingRecord(
                chunk_id=chunk.chunk_id,
                job_id=state.get("job_id") or "",
                tenant_id=state.get("tenant_id"),
                project_id=state.get("project_id"),
                embedding_model=getattr(embedder, "model", "unknown"),
                embedding=vec,
            )
            for chunk, vec in zip(chunks, vectors)
        ]
        stores = get_stores()
        await stores.embeddings.save_embeddings(records)
        return {"embeddings_indexed": len(records), "embeddings_status": "ok"}
    except Exception as exc:  # pragma: no cover - provider/store dependent
        logger.warning("embedding generation failed: %s", type(exc).__name__)
        return {"embeddings_indexed": 0, "embeddings_status": "error"}


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
    emb_stats = await _maybe_generate_embeddings(state, chunks)
    if emb_stats:
        stats = {**stats, **emb_stats}
    return {
        "source_index_id": job_id,
        "retrieval_stats": stats,
    }
