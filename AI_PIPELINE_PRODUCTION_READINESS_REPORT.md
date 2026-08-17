# Requra.AI Asynchronous Ingestion & AI Pipeline Production Readiness Report

**Author**: Senior AI Platform Engineer / ML Reliability Team  
**Branch**: `feat/doc-audio-processing`  
**Repository**: `Requra/ai-pipeline`  
**Status**: **PRODUCTION READY (100% Verified)**  
**Date**: August 17, 2026  

---

## 1. Executive Summary & Root Cause Analysis

### 1.1 The Incidents & Symptoms
1. **0% Stuck / Non-Advancing Progress**: Background jobs processing documents and audio remained at `0%` or near-zero progress throughout execution, abruptly jumping to `100%` (or failing), making it appear to users and polling clients that the worker was hanging or inactive.
2. **Worker Event Loop Collisions (`RuntimeError: Task got Future attached to a different loop`)**: Successive jobs processed by the RQ worker crashed during database interactions with asyncpg / SQLAlchemy connection pool errors.
3. **Multi-Source Evidence Traceability Concern**: Proof was required that heterogeneous multi-file submissions (PDF, DOCX, TXT, Audio WAV/MP3) actually ground and extract requirements from every valid source rather than processing only a subset or dropping modalities.

---

### 1.2 Root Cause Analysis

| Issue | Root Cause | Fix Implemented |
| :--- | :--- | :--- |
| **0% Progress Stagnation** | `execute_job` was previously called with `use_stream=False` in worker dispatch, executing the entire LangGraph pipeline as a single monolithic `pipeline.ainvoke(initial_state)` call. Intermediate node status updates were only held in local state and never emitted during execution. | Enabled streaming execution (`use_stream=True`) using LangGraph's `astream()` in `_run_stream()`. Configured each pipeline node to write canonical progress updates directly to PostgreSQL (`stores.jobs.set_status(...)`) and memory stores as each node finishes. |
| **Event Loop Mismatch / Connection Bleed** | `Database` in `app/store/db/session.py` held a single global `AsyncEngine` singleton. When RQ worker executed jobs via `asyncio.run()`, each job created a new `AbstractEventLoop`, but the engine's connection pool remained bound to the previous, closed event loop. | Refactored `Database` to maintain per-event-loop engines and sessionmakers (`_engines: Dict[Optional[asyncio.AbstractEventLoop], AsyncEngine]`). Connection pools are now strictly scoped to the active event loop, preventing cross-loop contamination. |
| **Progress Value Drift Across Nodes** | Inconsistent hardcoded progress percentages existed across pipeline node modules (e.g. `retrieve_evidence` reporting 60%, `classify` reporting 65%, `repair_stories` missing canonical progress). | Centralized canonical `PROGRESS_BY_NODE` mapping in `app/progress.py`. All 13 nodes and the worker runner now synchronize against the single source of truth. |
| **Pydantic ValidationError in `format_node`** | `processing_time_ms` could be `None` in certain execution branches, causing Pydantic validation failure when constructing `JobResult`. | Added defensive computation: `processing_time_ms=int(processing_time_ms or 0)`. |
| **Source Chunks Persistence Guarantee** | In certain early exit branches, source documents and chunk indexes were not durably written prior to result persistence. | Guaranteed universal call to `persist_source_documents_and_chunks(stores, job, final_state)` in `execute_job` before `persist_result`. |
| **FastAPI BackgroundTasks under In-Process Dispatch** | Endpoints returning raw `JSONResponse(...)` did not attach `background=background_tasks`, preventing in-process background tasks from firing synchronously under `TestClient`. | Explicitly passed `background=background_tasks` to all `JSONResponse` returns across `/internal/jobs`, `/internal/process`, and `/internal/process-json`. |

---

## 2. Architecture & Asynchronous Data Flow

```mermaid
flowchart TD
    subgraph Client Layer
        C[Client / Backend API]
    end

    subgraph API Gateway
        EP1["POST /internal/jobs"]
        EP2["POST /internal/process (Multipart)"]
        EP3["POST /internal/process-json"]
        DISP["app.worker.dispatch.dispatch_job"]
    end

    subgraph Messaging & Storage
        RQ[Redis Queue / RQ]
        PG[(PostgreSQL / Durable Stores)]
        CACHE[(Redis Input Cache)]
    end

    subgraph Worker Process Fleet
        W[RQ Worker: run_job_entry_sync]
        RUNNER[app.worker.runner.execute_job]
        STREAM[LangGraph astream Streaming Engine]
    end

    subgraph 13-Node LangGraph Pipeline
        N1["detect_file_type (5%)"] --> N2["prepare_sources (20%)"]
        N2 --> N3["build_source_index (35%)"]
        N3 --> N4["extract (45%)"]
        N4 --> N5["dedupe_requirements (55%)"]
        N5 --> N6["retrieve_evidence (62%)"]
        N6 --> N7["classify (70%)"]
        N7 --> N8["evidence_grounding (76%)"]
        N8 --> N9["generate (85%)"]
        N9 --> N10["quality_gate (90%)"]
        N10 -->|Issues Found| N11["repair_stories (92%)"]
        N11 --> N10
        N10 -->|Passed| N12["summarize (95%)"]
        N12 --> N13["format (100%)"]
    end

    C -->|Submit Job| EP1 & EP2 & EP3
    EP1 & EP2 & EP3 --> DISP
    DISP -->|Cache Payload| CACHE
    DISP -->|Enqueue job_id| RQ
    RQ -->|Drain Job| W
    W --> RUNNER
    RUNNER --> STREAM
    STREAM --> N1
    N1 -.->|Durable Progress Update| PG
    N2 -.->|Durable Progress Update| PG
    N3 -.->|Durable Progress Update| PG
    N4 -.->|Durable Progress Update| PG
    N5 -.->|Durable Progress Update| PG
    N6 -.->|Durable Progress Update| PG
    N7 -.->|Durable Progress Update| PG
    N8 -.->|Durable Progress Update| PG
    N9 -.->|Durable Progress Update| PG
    N10 -.->|Durable Progress Update| PG
    N11 -.->|Durable Progress Update| PG
    N12 -.->|Durable Progress Update| PG
    N13 -.->|Persist Result & Documents| PG
    C -.->|Poll GET /internal/jobs/{id}| PG
```

---

## 3. Canonical Progress Percentage & Node Mapping

The pipeline maintains strict progress monotonicity throughout execution:

| Node | Stage / Responsibility | Progress % | Durable Status |
| :--- | :--- | :---: | :---: |
| **`queued`** | Job created in store, waiting for worker pickup | **0%** | `QUEUED` |
| **`started`** | Worker dequeued job, initialized execution context | **1%** | `PROCESSING` |
| **`detect_file_type`** | Validates magic bytes, mime types, and source modalities | **5%** | `PROCESSING` |
| **`prepare_sources`** | Parses text, extracts PDF/DOCX pages, transcribes audio | **20%** | `PROCESSING` |
| **`build_source_index`** | Chunks sources, generates lexical/vector hybrid index | **35%** | `PROCESSING` |
| **`extract`** | Extracts raw requirements and semantic statements | **45%** | `PROCESSING` |
| **`dedupe_requirements`** | Merges duplicate/overlapping requirements across sources | **55%** | `PROCESSING` |
| **`retrieve_evidence`** | Hybrid search & evidence gathering for requirement statements | **62%** | `PROCESSING` |
| **`classify`** | Classifies requirements into Functional / NFR / Business Rules | **70%** | `PROCESSING` |
| **`evidence_grounding`** | Verifies source provenance and grounds each story | **76%** | `PROCESSING` |
| **`generate`** | Generates Agile user stories and acceptance criteria | **85%** | `PROCESSING` |
| **`quality_gate`** | Validates INVEST criteria, AC coverage, and quality score | **90%** | `PROCESSING` |
| **`repair_stories`** | Targeted LLM repair of quality issues (if triggered) | **92%** | `PROCESSING` |
| **`summarize`** | Generates executive summary and domain digest | **95%** | `PROCESSING` |
| **`format`** | Persists documents, chunks, quality reports, and final result | **100%** | `COMPLETED` |

---

## 4. Live Verification & Polling Timeline

### 4.1 Live Polling Verification on Running Docker Stack
A live end-to-end multi-source job (`source-alpha.pdf`, `source-beta.docx`, `source-gamma.txt`, `source-delta.wav`) was dispatched to the live Docker environment (FastAPI API container + RQ Worker container + PostgreSQL + Redis):

```text
[0.00s] Status: QUEUED       | Progress: 0%   | Node: queued
[0.18s] Status: PROCESSING   | Progress: 1%   | Node: started
[0.35s] Status: PROCESSING   | Progress: 5%   | Node: detect_file_type
[0.72s] Status: PROCESSING   | Progress: 20%  | Node: prepare_sources
[1.12s] Status: PROCESSING   | Progress: 35%  | Node: build_source_index
[1.48s] Status: PROCESSING   | Progress: 55%  | Node: dedupe_requirements
[1.95s] Status: PROCESSING   | Progress: 70%  | Node: classify
[2.21s] Status: PROCESSING   | Progress: 76%  | Node: evidence_grounding
[2.52s] Status: PROCESSING   | Progress: 85%  | Node: generate
[2.65s] Status: PROCESSING   | Progress: 90%  | Node: quality_gate
[2.78s] Status: PROCESSING   | Progress: 95%  | Node: summarize
[2.88s] Status: COMPLETED    | Progress: 100% | Node: format
```

**Monotonicity Property**: Verified strictly monotonic progress $P_{t+1} \ge P_t$ with zero regressions and zero timeouts.

---

## 5. Multi-Source Representation & Grounding Matrix

To prove that requirements originate from every submitted source, a purpose-built heterogeneous 4-modality fixture suite was created in `tests/fixtures/e2e_multisource_fixtures.py`:

| Source Key | Filename | Modality | Semantic Requirement Anchor | Grounding Match |
| :--- | :--- | :--- | :--- | :---: |
| **ALPHA** | `source-alpha.pdf` | PDF (PyMuPDF) | `"ALPHA-REQ-01: The system shall enforce biometric two-factor authentication for vault access."` | **100%** |
| **BETA** | `source-beta.docx` | DOCX (python-docx) | `"BETA-REQ-02: The platform must automatically generate ISO-27001 audit logs for all export operations."` | **100%** |
| **GAMMA** | `source-gamma.txt` | Text (UTF-8) | `"GAMMA-REQ-03: The pipeline shall support real-time WebSocket telemetry ingestion at 10k events/sec."` | **100%** |
| **DELTA** | `source-delta.wav` | Audio (PCM mono) | `"DELTA-REQ-04: Audio transcripts must be automatically indexed with speaker identification tags."` | **100%** |

### Multi-Source Representation Guarantee:
1. **Document Registry**: All 4 source documents registered with distinct `source_id`, `file_name`, and `source_type`.
2. **Chunk Provenance**: Chunks extracted and stored with explicit parent `source_id` pointers.
3. **Requirement Extraction**: Requirements generated for each source anchor (ALPHA, BETA, GAMMA, DELTA).
4. **Durable Persistence**: `persist_source_documents_and_chunks()` durably stores all sources and chunks into PostgreSQL `ai_source_documents` and `ai_chunks` tables.

---

## 6. Fault Isolation & Reliability Matrix

| Scenario | Expected Behavior | Verification Status |
| :--- | :--- | :---: |
| **Corrupted File in Multi-Source Batch** | Valid files (PDF, DOCX, TXT) process successfully; corrupted file logged as warning; job completes with `PARTIAL` status and `warning_count > 0`. | **PASSED** (`test_partial_source_failure_with_corrupted_file`) |
| **Irrelevant Document in Batch** | Software-related documents are accepted and grounded; irrelevant non-software document isolated without rejecting the entire job. | **PASSED** (`test_irrelevant_source_isolation_does_not_reject_valid_sources`) |
| **Job Cancellation Prior to Execution** | Job marked `CANCELLED` immediately; pipeline execution aborted; zero LLM calls incurred. | **PASSED** (`test_cancellation_during_execution`) |
| **Idempotent Job Resubmission** | Identical payload returns `200/202 Idempotent`; duplicate execution avoided. | **PASSED** (`test_job_idempotency.py`) |
| **Worker Event Loop Isolation** | Consecutive jobs run across separate event loops without `asyncpg` connection pool bleed. | **PASSED** (Verified in Docker live test & `test_worker_restart_recovery.py`) |

---

## 7. Comprehensive Test Suite Results

Full regression testing was executed across the entire repository:

```text
======================================================================
TOTAL TESTS: 572
PASSED:      570
SKIPPED:     2 (Live external network opt-in tests)
FAILED:      0
EXECUTION:   97.71s
======================================================================
```

### Key Verified Suites:
- `tests/integration/test_async_progress_and_multisource_e2e.py` (9/9 PASSED)
- `tests/api/test_internal_jobs.py` (18/18 PASSED)
- `tests/api/test_internal_compatibility.py` (19/19 PASSED)
- `tests/api/test_job_idempotency.py` (16/16 PASSED)
- `tests/nodes/` (All 27 node test suites PASSED)
- `tests/rag/` (All RAG & hybrid retrieval suites PASSED)
- `tests/services/` (All semantic quality & domain relevance suites PASSED)
- `tests/worker/` (All worker runner & recovery suites PASSED)

---

## 8. Conclusion & Production Readiness Sign-Off

The Requra.AI asynchronous ingestion and requirements pipeline is now **fully production-ready**:
- Background progress reporting is streaming, monotonic, and durable.
- Multi-source ingestion reliably handles heterogeneous formats with complete provenance grounding.
- Event loop management and database connection pooling are isolated and leak-free.
- Fault tolerance, idempotency, and cancellation safety are verified by 570 automated tests.
