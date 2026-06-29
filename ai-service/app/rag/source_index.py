"""
Per-job source-index registry.

The LangGraph state must stay JSON-serializable, so it cannot hold a live
``LexicalRetriever`` (which carries the full corpus + stats). Instead the
``build_source_index`` node stores the retriever here, keyed by ``job_id``, and
keeps only a lightweight ``source_index_id`` handle in state. Downstream nodes
(``retrieve_evidence``) look the retriever back up by that handle.

This is a process-local, in-memory registry — appropriate for the single-process
demo service. It is bounded (FIFO eviction) so a long-running dev server does not
leak one retriever per job forever.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Sequence

from app.rag.lexical_retriever import LexicalRetriever
from app.schemas.items import SourceChunk

# Cap the number of retained indexes. Jobs are short-lived; this just bounds
# memory if cleanup is never called.
_MAX_INDEXES = 256

_REGISTRY: "OrderedDict[str, LexicalRetriever]" = OrderedDict()
_LOCK = threading.Lock()


def build_source_index(job_id: str, chunks: Sequence[SourceChunk]) -> Dict[str, Any]:
    """Build and register a retriever for ``job_id``; return lightweight stats.

    The returned dict is safe to store in graph state (all primitives).
    """
    retriever = LexicalRetriever(chunks)
    if job_id:
        with _LOCK:
            _REGISTRY[job_id] = retriever
            _REGISTRY.move_to_end(job_id)
            while len(_REGISTRY) > _MAX_INDEXES:
                _REGISTRY.popitem(last=False)  # FIFO eviction of oldest.

    return {
        "indexed_chunks": retriever.size,
        "vocabulary_size": len(retriever.stats.idf),
        "avg_chunk_tokens": round(retriever.stats.avg_doc_len, 2),
    }


def get_source_index(job_id: str) -> Optional[LexicalRetriever]:
    """Return the retriever for ``job_id`` (None if unknown/evicted)."""
    if not job_id:
        return None
    with _LOCK:
        return _REGISTRY.get(job_id)


def clear_source_index(job_id: str) -> None:
    """Drop the retriever for ``job_id`` if present."""
    if not job_id:
        return
    with _LOCK:
        _REGISTRY.pop(job_id, None)


def source_index_size() -> int:
    """Number of currently registered indexes (for diagnostics/tests)."""
    with _LOCK:
        return len(_REGISTRY)
