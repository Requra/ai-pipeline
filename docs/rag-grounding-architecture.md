# RAG Grounding Architecture

How the Requra AI pipeline uses Retrieval-Augmented Generation (RAG) — and why it
is **not** a chatbot.

## 1. Purpose: grounding, not Q&A

This pipeline transforms unstructured input (briefs, transcripts, documents) into
**structured requirements, classified types, user stories, acceptance criteria,
executive summaries, source traceability, quality warnings, and export rows**.

RAG here exists for one job: **source grounding**. Retrieval is used to

- attach and validate **evidence quotes** for every requirement,
- improve **traceability** between requirements/stories and the exact source
  chunks they came from, and
- **reduce hallucination** by preferring requirements that are supported by the
  source text.

It is explicitly **not** chatbot RAG: there is no conversational interface, no
free-form question answering, and no "ask the document" loop. The retriever
answers one structured query per requirement ("what source text supports this?"),
never an open user prompt.

## 2. Why no vector database

For the MVP the retriever is **in-memory and lexical (BM25)**:

- No hosted vector DB, no embeddings service, no network calls — the pipeline
  stays a self-contained internal microservice.
- Deterministic: identical input → identical scores → identical ordering, which
  makes evaluation and CI reproducible.
- Cheap and fast for the per-job corpus sizes we see (a handful to a few hundred
  chunks).

A heavier embedding-based retriever can be added later behind the same
`LexicalRetriever`-shaped interface without changing any node contracts.

## 3. The grounding flow

```
parse_to_chunks → build_source_index → extract → dedupe_requirements
    → retrieve_evidence → classify → evidence_grounding → generate → quality_gate
```

### Chunking (`parse_to_chunks`)
Documents become coordinate-aware `SourceChunk`s (chunk_id, text, char offsets,
optional page/speaker/timestamp). PDFs split on page breaks; text/DOCX use an
overlapping sliding window so requirements that straddle a boundary are not lost.

### Source index (`build_source_index`)
Chunks are tokenized (lowercased, stopword-filtered) and indexed with a
deterministic BM25 model (`app/rag/`). The IDF term uses the always-positive
`log(1 + (N - df + 0.5)/(df + 0.5))` form so retrieval still ranks sensibly when
a short document yields a single chunk. The (non-serializable) retriever lives in
a bounded per-job registry; the LangGraph state holds only a `source_index_id`
handle plus primitive `retrieval_stats`, so state stays JSON-serializable.

### Extraction grounding (`extract`)
The extractor must return a **verbatim quote** for every requirement. Quotes are
aligned back to the source chunk and graded:

- exact match → full confidence,
- fuzzy (whitespace/case) match → slight confidence penalty + review flag,
- no match → a source snippet is substituted, confidence is reduced, the
  requirement is flagged `needs_review`, and an `EXTRACT_WEAK_EVIDENCE` warning is
  raised. Evidence is never silently dropped.

Malformed model JSON gets one repair round (`app/utils/json_parsing.py`) before
the chunk is skipped, so a single bad chunk never crashes the job. Raw model
output is only logged at DEBUG and never in production.

### Deduplication (`dedupe_requirements`)
Chunk overlap and repetition produce duplicate requirements. Exact and
near-duplicate (token Jaccard ≥ 0.8) requirements are merged: evidence spans are
**unioned** (never dropped), the highest confidence and strongest priority win,
labels are unioned. Requirements that share text but name a **different actor**
are kept separate and flagged `POSSIBLE_DUPLICATE_REVIEW`.

### Evidence retrieval (`retrieve_evidence`)
For each (de-duplicated) requirement, a query is built from its text + actor +
goal and run against the index. The best supporting **sentence** from chunks not
already cited is attached as additional evidence — capped (≤ 4 evidence/req,
≤ 240 chars) so the final payload is not bloated by full-chunk dumps. Retrieval
records `evidence_match_score` and `quote_support_score`; weak support lowers
confidence and flags review.

### Evidence validation (`evidence_grounding`)
Validates that every requirement has at least one non-empty quote and that quotes
appear in the source chunks; failures become `QualityIssue`s and review flags.

### Quality gate (`quality_gate`)
Derives **real** scores from the above signals — traceability coverage,
groundedness (driven by `quote_support_score`), story completeness, acceptance-
criteria quality, duplicate risk, and an overall score — and emits meaningful
issues (missing/weak evidence, missing source ids, generic criteria, duplicate
stories, low-confidence classification). Nothing is faked.

## 4. Limitations (MVP)

- **Lexical only.** BM25 matches words, not meaning; paraphrased support without
  shared vocabulary may score low. (Mitigated by actor/goal-augmented queries.)
- **Per-process, in-memory index.** The retriever and job store live in the
  service process; they are not durable across restarts and are not shared across
  replicas. Fine for the single-process demo; swap in Redis/DB + embeddings for
  scale.
- **Quotes are source-language verbatim;** requirement text is translated to
  English, so a quote and its requirement text may be in different languages by
  design (traceability over uniformity).
