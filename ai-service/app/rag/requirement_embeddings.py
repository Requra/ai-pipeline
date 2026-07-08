"""
RequirementEmbeddingService for lazy embedding generation, caching, and similarity.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional

from app.rag.embeddings import get_embedder
from app.schemas.items import ExtractedRequirement

logger = logging.getLogger("app.rag.requirement_embeddings")


class RequirementEmbeddingService:
    @staticmethod
    async def ensure_requirement_embeddings(requirements: List[ExtractedRequirement]) -> None:
        """
        Lazily compute embeddings for a list of ExtractedRequirement objects.
        Only generates embeddings for requirements that do not already have one.
        Embeddings are set on the requirement objects in-place.
        """
        to_embed = [r for r in requirements if r.embedding is None]
        if not to_embed:
            logger.info("All requirements already have cached embeddings. Skipping computation.")
            return

        embedder = get_embedder()
        if not embedder:
            logger.warning("No embedder is configured or keys are missing. Cannot compute requirement embeddings.")
            return

        texts = [r.text for r in to_embed]
        model_name = getattr(embedder, "model", "unknown")
        logger.info(
            "Computing embeddings for %d requirement(s) using provider model '%s'...",
            len(to_embed), model_name
        )

        try:
            embeddings = await embedder.embed_documents(texts)
            for req, vector in zip(to_embed, embeddings):
                req.embedding = vector
            logger.info("Successfully computed and cached %d requirement embeddings.", len(to_embed))
        except Exception as exc:
            logger.error("Failed to generate requirement embeddings: %s", exc, exc_info=True)
            # We don't raise the exception to let the pipeline degrade gracefully
            # and fallback to Jaccard-based conflict retrieval.

    @staticmethod
    def similarity(a: Optional[List[float]], b: Optional[List[float]]) -> float:
        """Compute cosine similarity between two vector embeddings."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
