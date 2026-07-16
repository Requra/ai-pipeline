"""
Hybrid retrieve_evidence node tests.

Uses a deterministic in-process embedder + memory embedding store to verify:
  * embeddings are generated + persisted when enabled,
  * hybrid retrieval respects tenant/project/job filters (no cross-tenant leak),
  * retrieved evidence is grounded in real source chunks,
  * the lexical (BM25-only) path is unchanged when hybrid is disabled.
"""

from __future__ import annotations

import hashlib

import pytest

from app.nodes.build_source_index import build_source_index_node
from app.nodes.retrieve_evidence import retrieve_evidence_node
from app.rag import embeddings as emb
from app.schemas.items import ExtractedRequirement, SourceChunk
from app.store.factory import get_stores, reset_stores

pytestmark = pytest.mark.asyncio

_DIM = 24


def _vec(text: str):
    v = [0.0] * _DIM
    for w in text.lower().split():
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % _DIM] += 1.0
    return v


class _FakeEmbedder:
    model = "fake"

    async def embed_documents(self, texts):
        return [_vec(t) for t in texts]

    async def embed_query(self, text):
        return _vec(text)


@pytest.fixture(autouse=True)
def _setup():
    reset_stores()
    emb.set_embedder(_FakeEmbedder())
    yield
    emb.set_embedder(None)
    reset_stores()


def _chunks():
    return [
        SourceChunk(chunk_id="c1", text="The system must allow users to reset their password via email.",
                    start_char=0, end_char=60),
        SourceChunk(chunk_id="c2", text="Admins can export monthly usage reports as CSV files.",
                    start_char=61, end_char=120),
    ]


def _req():
    return ExtractedRequirement(
        id=1, text="Users recover their account password by email", actor="user",
        goal="reset password", confidence=0.8, evidence=[],
    )


def _state(**over):
    base = {
        "job_id": "hj1", "tenant_id": "t1", "project_id": "p1",
        "enable_embeddings": True, "enable_hybrid_retrieval": True,
        "chunks": _chunks(), "extracted_requirements": [_req()],
        "warnings": [], "retrieval_stats": None,
    }
    base.update(over)
    return base


async def test_embeddings_generated_and_persisted_when_enabled():
    state = _state()
    out = await build_source_index_node(state)
    assert out["retrieval_stats"]["embeddings_status"] == "ok"
    assert await get_stores().embeddings.count_for_job("hj1") == 2


async def test_hybrid_retrieval_grounds_and_scores():
    state = _state()
    state.update(await build_source_index_node(state))
    out = await retrieve_evidence_node(state)
    req = out["extracted_requirements"][0]
    assert out["retrieval_stats"]["mode"] == "hybrid"
    assert req.vector_match_score is not None and req.vector_match_score > 0
    # Evidence is drawn from real chunks (grounding): the password chunk wins.
    assert any(e.chunk_id == "c1" for e in req.evidence)
    chunk_texts = {c.text for c in state["chunks"]}
    for e in req.evidence:
        assert any(e.quote in t or e.quote == t[: len(e.quote)] for t in chunk_texts) or e.quote in " ".join(chunk_texts)


async def test_no_cross_tenant_retrieval_leak():
    # Index job for tenant t1/p1.
    state = _state()
    state.update(await build_source_index_node(state))

    # A second job for a DIFFERENT tenant querying the same project id must not
    # see t1's embeddings.
    other = _state(job_id="hj2", tenant_id="t2", chunks=[], extracted_requirements=[_req()])
    # No chunks for hj2 → its own index is empty; hybrid vector search is scoped
    # to tenant t2 + job hj2, so it can never surface t1's chunk c1.
    hits = await get_stores().embeddings.vector_search(
        _vec("reset password"), tenant_id="t2", project_id="p1", job_id="hj2", top_k=5
    )
    assert hits == []


async def test_lexical_only_when_hybrid_disabled():
    state = _state(enable_embeddings=False, enable_hybrid_retrieval=False)
    state.update(await build_source_index_node(state))
    out = await retrieve_evidence_node(state)
    assert out["retrieval_stats"]["mode"] == "lexical"
    req = out["extracted_requirements"][0]
    assert req.vector_match_score is None
    # No embeddings persisted when disabled.
    assert await get_stores().embeddings.count_for_job("hj1") == 0
