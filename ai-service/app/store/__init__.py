"""Durable store layer for the AI processing service.

Public surface:
  * Domain records          — :mod:`app.store.models`
  * Store protocols + bundle — :mod:`app.store.base`
  * Backend selection        — :func:`app.store.factory.get_stores`
"""

from app.store.base import (
    ChunkStore,
    EmbeddingStore,
    JobStore,
    ResultStore,
    StoreBundle,
)
from app.store.factory import get_stores, reset_stores

__all__ = [
    "ChunkStore",
    "EmbeddingStore",
    "JobStore",
    "ResultStore",
    "StoreBundle",
    "get_stores",
    "reset_stores",
]
