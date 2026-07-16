"""
In-memory, dependency-free RAG for *source grounding* (not chatbot Q&A).

The pipeline uses retrieval to attach and validate evidence quotes and to improve
traceability between requirements/stories and their source chunks. It is:

  * deterministic (lexical BM25 scoring — no embeddings, no network),
  * in-memory and per-job (no external vector DB), and
  * robust to empty/degenerate input.

Public surface:
  * ``LexicalRetriever`` — score + top-k retrieval over a chunk set.
  * ``build_source_index`` / ``get_source_index`` / ``clear_source_index`` — a
    per-job registry that keeps the (non-serializable) retriever out of the
    LangGraph state, which only stores a ``source_index_id`` handle.
"""

from app.rag.lexical_retriever import LexicalRetriever
from app.rag.source_index import (
    build_source_index,
    clear_source_index,
    get_source_index,
    source_index_size,
)

__all__ = [
    "LexicalRetriever",
    "build_source_index",
    "get_source_index",
    "clear_source_index",
    "source_index_size",
]
