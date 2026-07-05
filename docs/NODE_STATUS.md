# AI Pipeline Node Status Report

This document provides a detailed breakdown of the implementation status for each node in the compiled 14-node LangGraph pipeline.

## Executive Summary

The AI requirements extraction pipeline is fully implemented, wired, and functional. All nodes (1 through 14) are complete. The pipeline supports real file ingestion (PDF, Word, TXT, and Audio transcription) and operates in two primary modes:
1. **Local/Development Mode**: Runs synchronously using process-local in-memory jobs and a deterministic BM25 lexical index (no external network or DB dependencies).
2. **Production Mode**: Runs asynchronously using a Redis/RQ queue worker fleet, storing all jobs, chunks, and requirements in PostgreSQL, and optionally running semantic/hybrid RAG searches via `pgvector` embeddings.

---

## Node Status Breakdown

| # | Node Name | Status | Description |
|---|---|---|---|
| 1 | `detect_file_type` | ✅ Implemented | Inspects file headers, MIME-types, or extensions to identify type (pdf, docx, txt, audio). |
| 2 | `ingest` | ✅ Implemented | Extracts text from raw streams (using `PyMuPDF` for PDF, `python-docx` for DOCX), normalizes text, masks PII, and runs relevance checks. |
| 3 | `transcribe` | ✅ Implemented | Transcribes audio bytes into text with speaker and timestamp markers (via Whisper-1 or Deepgram). |
| 4 | `parse_to_chunks` | ✅ Implemented | Segments documents into coordinate-aware `SourceChunk`s (overlapping windowing or PDF page splits). |
| 5 | `build_source_index` | ✅ Implemented | Compiles local lexical BM25 search indices and optionally generates semantic embeddings via pgvector. |
| 6 | `extract` | ✅ Implemented | Uses LLM to extract requirements, aligns verbatim evidence quotes, and handles JSON parsing repairs. |
| 7 | `dedupe_requirements` | ✅ Implemented | Cleans up duplicate requirements based on Token Jaccard similarity; unions evidence and raises actor conflict warnings. |
| 8 | `retrieve_evidence` | ✅ Implemented | Queries the indexes to attach additional evidence quotes. Supports lexical BM25 and pgvector hybrid search merging. |
| 9 | `classify` | ✅ Implemented | Categorizes requirements into Functional (FR), Non-Functional (NFR), or Business Rules (BR) with confidence scores. |
| 10 | `evidence_grounding` | ✅ Implemented | Programmatically verifies that all cited LLM quotes exist in the source document chunks. |
| 11 | `generate` | ✅ Implemented | Transforms requirements into formatted Agile User Stories with testable Given-When-Then criteria. |
| 12 | `quality_gate` | ✅ Implemented | Computes numerical quality metrics (traceability, groundedness, story completeness) and flags warnings. |
| 13 | `summarize` | ✅ Implemented | Generates a structured executive summary with key decisions, risks, assumptions, and stakeholders. |
| 14 | `format` | ✅ Implemented | Assembles the final versioned public contract (`JobResult`) and prepares spreadsheet rows for Excel/Jira export. |

---

## Infrastructure & Production Status

* **Graph Orchestration**: Orchestrated as an acyclic `StateGraph` with a raised recursion limit of 60 to prevent step budget exhaustion.
* **Database & Persistence**: Postgres stores data across 12+ schemas, including `ai_source_chunks` and `ai_source_chunk_embeddings` supporting `pgvector` similarity searches.
* **Idempotency & Retry**: Fully supported via fingerprint hashing of incoming payloads, atomic lock-guarded creations, and retry transitions.
* **Queue**: Redis-backed RQ (Redis Queue) handles background execution in production, while synchronous In-Process queue runs by default in development.

## Next Steps / Focus Areas
1. **Live Concurrency Verification**: Validate the PostgreSQL transactional database operations and RQ worker task-locking under heavy concurrent load in staging.
2. **Integration testing**: Ensure external API endpoints (/internal/*) remain fully verified through automated CI checks when scaling worker replicas.
