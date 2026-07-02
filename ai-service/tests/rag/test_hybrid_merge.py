"""Unit tests for the pure hybrid-merge ranking logic."""

from __future__ import annotations

from app.rag.hybrid import merge_hits
from app.schemas.items import RetrievedChunk, SourceChunk


def _bm25(chunk_id, score, text="lexical text"):
    return RetrievedChunk(chunk_id=chunk_id, text=text, score=score)


def test_agreement_between_signals_boosts_rank():
    chunks = {
        "c1": SourceChunk(chunk_id="c1", text="both agree here", start_char=0, end_char=10),
        "c2": SourceChunk(chunk_id="c2", text="lexical only", start_char=0, end_char=10),
        "c3": SourceChunk(chunk_id="c3", text="vector only", start_char=0, end_char=10),
    }
    bm25 = [_bm25("c1", 5.0), _bm25("c2", 6.0)]
    vector = [{"chunk_id": "c1", "score": 0.9}, {"chunk_id": "c3", "score": 0.8}]

    merged = merge_hits(bm25, vector, chunks, top_k=3)
    ids = [m.chunk_id for m in merged]
    # c1 appears in both → agreement boost should rank it first.
    assert ids[0] == "c1"
    assert "vector" in merged[0].sources and "bm25" in merged[0].sources
    assert merged[0].vector_score == 0.9


def test_vector_only_chunk_gets_text_from_map():
    chunks = {"c9": SourceChunk(chunk_id="c9", text="semantic recall text", start_char=0, end_char=10)}
    merged = merge_hits([], [{"chunk_id": "c9", "score": 0.7}], chunks, top_k=3)
    assert merged[0].chunk_id == "c9"
    assert merged[0].text == "semantic recall text"  # real source text, not invented
    assert merged[0].sources == ["vector"]


def test_top_k_is_respected():
    chunks = {f"c{i}": SourceChunk(chunk_id=f"c{i}", text=f"t{i}", start_char=0, end_char=2) for i in range(5)}
    bm25 = [_bm25(f"c{i}", float(5 - i)) for i in range(5)]
    merged = merge_hits(bm25, [], chunks, top_k=2)
    assert len(merged) == 2
    assert merged[0].chunk_id == "c0"  # highest lexical score first
