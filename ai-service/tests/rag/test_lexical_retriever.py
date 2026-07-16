"""Phase 2 — lexical scoring + retriever determinism and robustness."""

from __future__ import annotations

from app.rag.lexical_retriever import LexicalRetriever
from app.rag.scoring import bm25_score, build_corpus_stats, tokenize
from app.schemas.items import RetrievedChunk, SourceChunk


def _chunk(cid: str, text: str, idx: int = 0) -> SourceChunk:
    return SourceChunk(chunk_id=cid, text=text, start_char=idx, end_char=idx + len(text))


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def test_tokenize_drops_stopwords_and_short_tokens():
    toks = tokenize("The system SHALL export a PDF to S3")
    assert "system" in toks
    assert "export" in toks
    assert "pdf" in toks
    assert "the" not in toks  # stopword
    assert "a" not in toks    # stopword + length 1


def test_tokenize_empty_is_safe():
    assert tokenize("") == []
    assert tokenize("   ") == []
    assert tokenize(None) == []  # type: ignore[arg-type]


def test_bm25_positive_on_single_doc_corpus():
    docs = [tokenize("the system must export invoices to pdf")]
    stats = build_corpus_stats(docs)
    score = bm25_score(tokenize("export pdf"), docs[0], stats)
    assert score > 0.0


def test_bm25_zero_when_no_overlap():
    docs = [tokenize("the system must export invoices")]
    stats = build_corpus_stats(docs)
    assert bm25_score(tokenize("weather forecast"), docs[0], stats) == 0.0


# --------------------------------------------------------------------------
# retriever
# --------------------------------------------------------------------------

def test_retrieve_returns_relevant_chunk_first():
    chunks = [
        _chunk("c0", "Users can reset their password via email.", 0),
        _chunk("c1", "The system must export monthly invoices to PDF for finance.", 100),
        _chunk("c2", "Admins manage role-based access control settings.", 200),
    ]
    retriever = LexicalRetriever(chunks)
    results = retriever.retrieve("export invoices to PDF", top_k=3)
    assert results, "expected at least one hit"
    assert isinstance(results[0], RetrievedChunk)
    assert results[0].chunk_id == "c1"
    assert results[0].score > 0.0


def test_retrieve_respects_top_k():
    chunks = [_chunk(f"c{i}", f"requirement number {i} about data export", i * 10) for i in range(6)]
    retriever = LexicalRetriever(chunks)
    results = retriever.retrieve("data export requirement", top_k=2)
    assert len(results) == 2


def test_retrieve_is_deterministic_and_score_ordered():
    chunks = [
        _chunk("c0", "payment processing and refunds", 0),
        _chunk("c1", "payment payment payment gateway integration", 50),
        _chunk("c2", "user profile settings", 100),
    ]
    retriever = LexicalRetriever(chunks)
    first = retriever.retrieve("payment", top_k=3)
    second = retriever.retrieve("payment", top_k=3)
    # Deterministic across calls.
    assert [r.chunk_id for r in first] == [r.chunk_id for r in second]
    # Monotonically non-increasing scores.
    scores = [r.score for r in first]
    assert scores == sorted(scores, reverse=True)
    # The chunk repeating the term ranks above the single-mention chunk.
    assert first[0].chunk_id == "c1"


def test_retrieve_empty_chunks_is_safe():
    retriever = LexicalRetriever([])
    assert retriever.size == 0
    assert retriever.retrieve("anything", top_k=5) == []


def test_retrieve_empty_query_is_safe():
    retriever = LexicalRetriever([_chunk("c0", "some requirement text here", 0)])
    assert retriever.retrieve("", top_k=5) == []
    assert retriever.retrieve("   ", top_k=5) == []


def test_retriever_skips_blank_text_chunks():
    chunks = [_chunk("c0", "   ", 0), _chunk("c1", "real export requirement text", 10)]
    retriever = LexicalRetriever(chunks)
    assert retriever.size == 1
    results = retriever.retrieve("export", top_k=5)
    assert [r.chunk_id for r in results] == ["c1"]
