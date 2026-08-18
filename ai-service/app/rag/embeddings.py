"""
Embedding provider abstraction for semantic (vector) retrieval.

Opt-in: only used when a job enables embeddings/hybrid retrieval. The default
MVP path is lexical (BM25) and never calls an embedding provider, so this module
imports its provider SDK lazily and is safe to import with no keys configured.

The provider is chosen by ``EMBEDDING_PROVIDER`` (openai | openrouter) and uses
the OpenAI-compatible embeddings API. A test/override hook (:func:`set_embedder`)
lets the suite inject a deterministic embedder with no network calls.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Protocol

from app.config import embedding_key_for, settings

logger = logging.getLogger("app.rag.embeddings")


class Embedder(Protocol):
    model: str

    async def embed_documents(self, texts: List[str]) -> List[List[float]]: ...

    async def embed_query(self, text: str) -> List[float]: ...


class OpenAICompatibleEmbedder:
    """Embedder backed by the OpenAI-compatible embeddings API (openai/openrouter)."""

    def __init__(self, provider: str, model: str, api_key: str) -> None:
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from langchain_openai import OpenAIEmbeddings

            kwargs = {"model": self.model, "api_key": self._api_key}
            if self.provider == "openrouter":
                kwargs["base_url"] = settings.OPENROUTER_BASE_URL
            self._client = OpenAIEmbeddings(**kwargs)
        return self._client

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        return await client.aembed_documents(list(texts))

    async def embed_query(self, text: str) -> List[float]:
        client = self._get_client()
        return await client.aembed_query(text)


class ITIEmbedder:
    """Embedder backed by the ITI Bedrock Gateway embedding API."""

    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        base = settings.ITI_BASE_URL.rstrip("/")
        if base.endswith("/student"):
            base = base[:-8]
        if "/api/v1" not in base:
            base = f"{base}/api/v1"
        self.base_url = base
        self.api_key = api_key
        self.timeout = settings.PROVIDER_TIMEOUT_SECONDS

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        import httpx
        url = f"{self.base_url}/student/embed"
        payload = {
            "model_id": self.model,
            "texts": list(texts),
            "input_type": "search_document"
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            res = resp.json()
            emb = res.get("embedding") or res.get("embeddings")
            if not emb:
                raise ValueError(f"No embedding found in response: {res}")
            if isinstance(emb, list):
                if len(emb) > 0 and isinstance(emb[0], list):
                    return emb
                else:
                    return [emb]
            raise ValueError(f"Unexpected embedding format: {emb}")

    async def embed_query(self, text: str) -> List[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]


# Test/override hook — when set, get_embedder() returns this instead.
_override: Optional[Embedder] = None


def set_embedder(embedder: Optional[Embedder]) -> None:
    global _override
    _override = embedder


def get_embedder() -> Optional[Embedder]:
    """Return a configured embedder, or None when embeddings are unusable."""
    if _override is not None:
        return _override
    if not settings.ENABLE_EMBEDDINGS:
        return None
    provider = settings.EMBEDDING_PROVIDER
    key = embedding_key_for(provider)
    if not key:
        logger.warning("embeddings requested but no API key for provider '%s'", provider)
        return None
    if provider == "iti":
        return ITIEmbedder(settings.EMBEDDING_MODEL, key)
    return OpenAICompatibleEmbedder(provider, settings.EMBEDDING_MODEL, key)
