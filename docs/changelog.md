# Changelog - Requra AI Pipeline

This changelog records key transitions, feature integrations, and commit-by-commit changes in the Requra AI Pipeline.

---

## 1. [Commit `d724957`] docs(pipeline): document RAG-grounded MVP production flow
* **Goal:** Deliver production documentation.
* **Changes:** Added production architecture overviews, node references, contract annotations, and README setup guides.

## 2. [Commit `98623a8`] test(pipeline): add MVP regression fixtures and evaluation harness
* **Goal:** Introduce quality regression testing and run performance thresholds.
* **Changes:**
  * Added 5 test fixtures in `tests/fixtures/`.
  * Created `scripts/evaluate_pipeline.py` to run the deterministic evaluation harness.
  * Added the regression test suite `tests/test_mvp_quality.py`.
  * Fixed a status reporting bug on text rejection.

## 3. [Commit `6f7ad05`] feat(output): polish summary and export-ready rows
* **Goal:** Correct mapping of user story types, enrich exports with quality data, and inject digest into summaries.
* **Changes:**
  * Mapped User Story types correctly based on requirement labels instead of defaulting to "Functional".
  * Enriched Excel/Jira exports with requirement IDs, confidence, quality scores, and source quotes.
  * Fed requirements and stories digest into the `summarize` node.

## 4. [Commit `804d8d2`] feat(quality): add groundedness and traceability scoring
* **Goal:** Calculate numerical quality scores and add an optional quality report in the V1 contract.
* **Changes:**
  * Created `app/services/quality_scoring.py` with scoring logic.
  * Integrated scoring computations in `quality_gate_node` (`quality_gate.py`).
  * Added the `quality_report` field to `JobResult`.

## 5. [Commit `4ba8dce`] feat(generate): validate and repair generated stories
* **Goal:** Improve User Story quality, avoid generic fallback descriptions, and enforce testability.
* **Changes:**
  * Created prompt template `generate_user_stories_v2.md` enforcing detailed Given-When-Then criteria.
  * Created `story_validator.py` to detect generic criteria or duplicates.
  * Overhauled fallback story generation to construct type-aware acceptance criteria programmatically.

## 6. [Commit `e6761cd`] feat(rag): retrieve supporting evidence for requirements
* **Goal:** Strengthen traceability by appending extra context snippets to requirements before classification.
* **Changes:**
  * Created `retrieve_evidence_node` to execute queries against the index.
  * Appended matching sentence snippets as additional evidence capped at 4 spans (max 240 chars).
  * Penalized confidence if a requirement had zero grounded quotes and zero search hits.

## 7. [Commit `a41a38b`] feat(requirements): dedupe extracted requirements before classification
* **Goal:** Merge duplicate entries while preserving source references.
* **Changes:**
  * Created `dedupe_requirements_node` and wired it between extraction and classification.
  * **Critical Bug Fix:** Raised the LangGraph compilation `recursion_limit` config to `60` to accommodate the 14-node chain.

## 8. [Commit `8672649`] feat(extract): strengthen grounded extraction and JSON repair
* **Goal:** Prevent LLM hallucinations, support JSON repairs, and stop raw document leaks in standard logs.
* **Changes:**
  * Created prompt template `extract_requirements_v2.md` enforcing verbatim quotes.
  * Added `loads_with_llm_repair` in `app/utils/json_parsing.py` to recover from malformed JSON outputs.
  * Implemented strict whitespace-insensitive quote alignment checks via `align_quote_with_kind()`.
  * Moved raw LLM inputs/outputs to `DEBUG` logs behind `DEBUG_LLM_IO` to avoid document leaks.

## 9. [Commit `24b2cc4`] feat(rag): add in-memory source index and lexical retrieval
* **Goal:** Establish the foundation of the lexical RAG retriever.
* **Changes:**
  * Created `app/rag/scoring.py` containing the core BM25 scorer, tokenizers, and stopword filters.
  * Created `app/rag/lexical_retriever.py` containing `LexicalRetriever`.
  * Created `app/rag/source_index.py` to manage a FIFO local index registry.
  * Created `build_source_index_node` to compile search indexes.

## 10. [Commit `b352e44`] fix(api): harden async job lifecycle and status contract
* **Goal:** Safe, predictable async API handling, validation of caller-provided job IDs, and system status health reporting.
* **Changes:**
  * Created `JobStore` abstract interface and a concrete thread-safe `MemoryJobStore` implementation in `job_store.py`.
  * Added a `/ready` endpoint in `main.py` for health diagnostics.
  * Sanitized and validated job IDs against `^[A-Za-z0-9._-]{1,128}$`.
