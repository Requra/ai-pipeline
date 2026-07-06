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

## 2. Retrieval Databases: Lexical & Semantic (pgvector)

For local/development runs, the retriever operates as an **in-memory and lexical (BM25)** index to remain self-contained and reproducible without external database dependencies.

In production mode, the pipeline supports a hybrid retrieval system backed by **PostgreSQL and pgvector**:
- **Durable Storage**: Source documents, chunks, and embeddings are stored and queried from Postgres.
- **Hosted Embeddings**: Supports generating semantic vectors via embedding models.
- **Hybrid RAG**: Combines lexical (BM25) and semantic vector search (`pgvector` cosine similarity) using a rank-merging algorithm (`merge_hits`) that boosts agreements between both systems.
- **Isolation**: Vector searches are strictly tenant- and project-isolated.

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
Chunk overlap and repetition produce duplicate requirements. Exact and near-duplicate requirements are merged based on a token-based Jaccard similarity calculation:
$$\text{Jaccard}(A, B) = \frac{|A \cap B|}{|A \cup B|}$$
If the Jaccard score $\ge 0.8$, the requirements are merged: evidence spans are **unioned** (never dropped), the highest confidence and strongest priority win, and labels are unioned. Requirements that share text but name a **different actor** (e.g. "Customer" vs "Administrator") are kept separate and flagged `POSSIBLE_DUPLICATE_REVIEW` for manual inspection.

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

## 4. Limitations & Production Scope

- **Lexical only (Local/Dev default).** The local/dev mode is BM25-only and matches words, not meaning; paraphrased support without shared vocabulary may score low. This is resolved in production when hybrid/semantic embeddings are enabled.
- **Per-process, in-memory index (Local/Dev default).** The local/dev job store is not durable across restarts and is not shared across replicas. In production, this is solved by deploying a PostgreSQL database and Redis/RQ queue worker fleet.
- **Quotes are source-language verbatim;** requirement text is translated to English, so a quote and its requirement text may be in different languages by design (traceability over uniformity).

## 5. Metric Formula Reference (Quality Gate)

The quality gate generates a `QualityReportV1` containing six metrics:

### 5.1. Groundedness Score
Measures the proportion of verbatim quotes that exist in the source document.
$$\text{Groundedness} = \frac{1}{|R|} \sum_{r \in R} \text{quote\_support\_score}(r)$$
Where $\text{quote\_support\_score}(r)$ is the fraction of requirement $r$'s quotes successfully matched in the source text. If no retrieval was executed, it defaults to $1.0$ if the requirement has evidence, and $0.0$ if it has none.

### 5.2. Traceability Coverage
Measures the proportion of generated user stories that link back to at least one source requirement.
$$\text{Traceability} = \frac{|\{s \in S \mid \text{source\_requirement\_ids}(s) \neq \emptyset\}|}{|S|}$$

### 5.3. Story Completeness
Measures the proportion of user stories containing a title, a valid description, and at least two acceptance criteria.
$$\text{Completeness} = \frac{|\{s \in S \mid \text{is\_complete}(s)\}|}{|S|}$$

### 5.4. Acceptance Criteria Quality
Measures the proportion of acceptance criteria that are descriptive rather than generic boilerplate.
$$\text{AC Quality} = \frac{|\{c \in C_{\text{all}} \mid \neg\text{is\_generic}(c)\}|}{|C_{\text{all}}|}$$
* *Generic criteria* are flagged if they contain phrases like "works as expected", "implemented as specified", or are less than 15 characters long.

### 5.5. Duplicate Risk
Measures the proportion of duplicate user stories (identical titles and descriptions).
$$\text{Duplicate Risk} = \frac{|\text{Duplicate Stories}|}{|S|}$$

### 5.6. Overall Quality Score
The arithmetic mean of the five dimensions:
$$\text{Overall Score} = \frac{\text{Traceability} + \text{Groundedness} + \text{Completeness} + \text{AC Quality} + (1.0 - \text{Duplicate Risk})}{5}$$
