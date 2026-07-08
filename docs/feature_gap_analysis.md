# Requra.AI — Feature Gap Analysis: Document vs. Codebase

> Cross-reference of every feature, enhancement, and capability described in the project document
> against the actual implementation in `ai-service/`. Items are verified against source code.

---

## 🔴 High Impact — Fills Real Production Gaps

These features are explicitly described in the project document as core capabilities but are **not implemented** in the codebase. They directly affect production readiness, data quality, or the user-facing workflow.

---

### 1. Semantic Conflict Detection (Phase 2 Intelligence Layer)

| | |
|---|---|
| **Document Says** | _"Enhanced semantic conflict detection will compare normalized requirements by actor, feature, action, constraint, condition, priority, and scope. The system groups requirements that refer to the same feature or user goal, then checks for mutually exclusive rules, incompatible constraints, or opposite permissions."_ (Section 13, Phase 2) |
| **Code Has** | [dedupe_requirements.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/nodes/dedupe_requirements.py) only compares requirements using **token-level Jaccard similarity** (≥0.8 threshold) and a basic actor name mismatch check (`_actors_conflict`). It flags `POSSIBLE_DUPLICATE_REVIEW` when two similar requirements have different actors. |
| **Gap** | There is **no logical/semantic contradiction detection**. Example: _"Users can log in with email and password"_ vs _"Users can only log in with Google"_ — these use different words (low Jaccard) but are logically contradictory. The system would not flag this. |
| **Implementation** | Add a new pipeline node (e.g., `detect_conflicts`) or extend `dedupe_requirements` to: (1) Group requirements by feature/goal using LLM-based semantic clustering, (2) Within each group, use an LLM call to check for mutually exclusive rules, (3) Flag conflicts with severity, source quotes, and a clarification question for BA/PM review. |

---

### 2. Human-in-the-Loop Review Workflow (Review Status Management)

| | |
|---|---|
| **Document Says** | _"Each requirement or user story can be marked as generated, needs_review, edited, approved, rejected, or exported. If the AI produces an incorrect or low-confidence story, the BA/PM can edit it, reject it, regenerate it, or approve it after correction. Only approved items are eligible for final export."_ (Section 4) |
| **Code Has** | The AI pipeline only sets `needs_review: bool` and `review_reason: str` on requirements. There are **no API endpoints** for editing, approving, rejecting, or regenerating individual requirements or stories. There is no `review_status` field on `RequirementV1` or `UserStoryV1`. The `JobResult` is a frozen, immutable blob. |
| **Gap** | The entire post-processing review lifecycle is missing from the AI service. There are no `PATCH /internal/jobs/{id}/requirements/{req_id}` or `POST /internal/jobs/{id}/stories/{story_id}/regenerate` endpoints. |
| **Implementation** | **Division of Responsibilities:**<br>1. **CRUD Operations (Edit, Approve, Reject, Export)**: Should live in the **.NET Backend**. Once generated, data is copied into backend database tables where users mutate the state, keeping the AI service stateless.<br>2. **Regeneration Endpoint**: Should live in the **AI Service (FastAPI)**, exposing an endpoint like `POST /internal/requirements/regenerate-story` that returns a fresh user story based on requirement text and human feedback.<br><br>**Architecture Flow Options:**<br>- **Option A (Recommended: Post-Processing)**: The AI runs to completion, flagging low-confidence items. All adjustments are made on a React dashboard afterward.<br>- **Option B (Advanced: Interactive pause/resume)**: Use LangGraph's `interrupt()` to freeze the graph when vague specifications are detected, prompting the user in a UI stepper modal before continuing downstream. |

> [!IMPORTANT]
> This is a **core business workflow** described in the project document. Without it, the AI service is a one-shot pipeline with no human approval gate before export.

---

### 3. Self-Correction Loops (Quality-Driven Re-Generation)

| | |
|---|---|
| **Document Says** | The document describes LangGraph as supporting _"cyclic processing pipelines"_ and _"stateful, multi-actor applications"_ (Section 5). The Q&A section explicitly flags: _"If a user story fails validation, the graph does not loop back to the generation node with instructions to repair itself."_ (Q8 Missing) |
| **Code Has** | The pipeline in [pipeline.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/graph/pipeline.py) is a strictly **linear DAG** — `quality_gate → summarize → format → END`. If the quality gate detects low scores or failed stories, it only records `QualityIssue` entries and downgrades the status to `partial`. It never loops back. |
| **Gap** | No cyclic self-correction. The graph topology has no edge from `quality_gate` back to `generate`. |
| **Implementation** | Add a conditional edge after `quality_gate`: if `overall_score < threshold` AND `repair_attempt < max_repairs`, route back to a `repair` node that re-prompts the LLM with the specific quality issues as instructions (e.g., _"Story US-3 has generic acceptance criteria: rewrite them"_). After repair, re-run `quality_gate`. Limit to 1-2 repair cycles to prevent infinite loops. |

---

### 4. Dynamic LLM Provider Fallback

| | |
|---|---|
| **Document Says** | Q1 explicitly flags: _"If OpenRouter/GPT-4o-mini fails or rate-limits during a pipeline run, there is no automatic fallback mechanism to switch to Groq or Gemini; the pipeline execution will fail."_ (Q1 Missing) |
| **Code Has** | [llm.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/llm.py) reads `LLM_PROVIDER` once and returns a single client. If that provider's API call fails mid-pipeline (rate limit, timeout, outage), the node raises an exception and the job fails. |
| **Gap** | No retry-with-fallback logic. A single provider outage kills the entire job. |
| **Implementation** | Wrap `get_llm()` with a fallback chain: try the primary provider, on `RateLimitError`/`APIConnectionError`/`Timeout`, automatically instantiate the next provider in a configured fallback list (e.g., `openrouter → openai → groq`). Add a `FALLBACK_LLM_PROVIDERS` env var. Log which provider was ultimately used. |

---

### 5. Multi-Document Awareness (Per-File Tracking)

| | |
|---|---|
| **Document Says** | Section 8 states that the backend provides `source_documents` references. The response contract includes `SourceRefV1` with `source_id`, `document_name`, and `page`. |
| **Code Has** | When multiple `source_documents` are submitted, the worker concatenates all texts into one string (`"\n\n".join(texts)` in [state.py:L166](file:///d:/ITI/GP/ai-pipeline/ai-service/app/worker/state.py#L166)). The resulting `SourceChunk`s have no `document_id` field linking them back to the original file. |
| **Gap** | The output `JobResult` cannot tell the frontend which requirement came from which uploaded file. Page numbers are meaningless when documents are merged. |
| **Implementation** | Add `document_id: Optional[str]` to `SourceChunk`. When building chunks from multiple documents, prefix each chunk's page numbers and tag its `document_id`. Propagate `document_id` through extraction and evidence into the final `SourceRefV1`. |

---

### 6. Callback Retry with Exponential Backoff

| | |
|---|---|
| **Document Says** | The production architecture doc (Section 12) explicitly lists _"a durable outbox/retry for callbacks is a future enhancement"_ as a known limitation. |
| **Code Has** | `_maybe_callback` in [runner.py:L327-349](file:///d:/ITI/GP/ai-pipeline/ai-service/app/worker/runner.py#L327-L349) fires one `POST` and if it fails, logs a warning. The result is silently lost if the backend is temporarily down. |
| **Gap** | Fire-and-forget callback. No retry, no outbox. |
| **Implementation** | Add a retry loop (3 attempts, 2s → 4s → 8s backoff). Optionally, persist failed callbacks to `ai_job_events` with `event_type=callback_pending` and add a cleanup task that re-attempts pending callbacks periodically. |

---

### 7. CSV Export Format

| | |
|---|---|
| **Document Says** | _"Excel/CSV exports and Jira-ready rows"_ (Sections 2, 4, 10). CSV is mentioned alongside Excel throughout the document. |
| **Code Has** | The format node builds `ExcelExportV1` and `JiraExportV1` objects. There is **no** `CsvExportV1` model, no CSV generation logic, and no CSV-related code anywhere in the codebase. |
| **Gap** | CSV export is mentioned in the project vision but not implemented. |
| **Implementation** | Add a `CsvExportV1` model to `items.py` and generate CSV-formatted rows in the format node (the data is the same as `ExcelExportV1.rows`, just serialized differently). |

---

## 🟡 Medium Impact — Improves Quality & Usability

These features are described or implied in the document and would meaningfully improve the system, but the pipeline can function without them.

---

### 8. Langfuse Observability Integration

| | |
|---|---|
| **Document Says** | _"Langfuse integration for prompt tracing, latency monitoring, token usage, cost analysis, and debugging."_ (Section 11) |
| **Code Has** | Zero references to `langfuse` anywhere in the codebase. LangSmith tracing is configured in `.env` but Langfuse is not. |
| **Gap** | Langfuse integration is entirely unimplemented. |
| **Implementation** | Install `langfuse` SDK, wrap LLM calls with Langfuse tracing decorators, and configure via `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` env vars. |

---

### 9. Semantic Section-Aware Chunking

| | |
|---|---|
| **Document Says** | Q5 flags: _"The pipeline does not understand document hierarchy (e.g., splitting by markdown headings, HTML tags, or DOCX list sections). This can cause tables or list elements to be split across different chunks."_ |
| **Code Has** | [parse_to_chunks.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/nodes/parse_to_chunks.py) uses a simple character-based sliding window (`CHUNK_SIZE_CHARS=3000`, `CHUNK_OVERLAP_CHARS=500`). PDFs split by page. No awareness of headings, sections, tables, or list boundaries. |
| **Gap** | Tables, numbered lists, and section blocks can be cut in half across chunk boundaries, breaking evidence quality. |
| **Implementation** | Enhance the text chunker to detect section boundaries (e.g., markdown headings `## ...`, numbered lists `1.`, DOCX paragraph styles) and prefer splitting at these natural breakpoints. Fall back to the sliding window only when a section exceeds `CHUNK_SIZE_CHARS`. |

---

### 10. Language Detection and Auto-Configuration

| | |
|---|---|
| **Document Says** | The pipeline processes Arabic/English mixed-language audio (Section 5 mentions _"Egyptian Arabic/English"_ for Deepgram). `SourceDocumentV1` has a `language` field. |
| **Code Has** | The `language` field on `SourceDocumentV1` is hardcoded to `"en"` as a default. No language detection runs during ingest. |
| **Gap** | Language is never detected, even though the pipeline processes non-English documents. |
| **Implementation** | Add a lightweight language detection step (e.g., `langdetect` library) during the ingest node. Set the `language` field accurately and potentially adjust BM25 stopword lists. |

---

### 11. Real-Time Progress via SSE

| | |
|---|---|
| **Document Says** | Section 11 mentions _"job status tracking; node-level progress logging"_. The backend must know job progress in real-time. |
| **Code Has** | Progress is tracked per-node in `PROGRESS_BY_NODE` and updated in the DB, but the backend can only discover it by repeatedly polling `GET /status/{job_id}`. |
| **Gap** | No push-based real-time progress notification. |
| **Implementation** | Add `GET /internal/jobs/{job_id}/stream` using FastAPI's `StreamingResponse` with Server-Sent Events (SSE). The worker already updates progress in the DB per-node — the SSE endpoint would poll the DB or listen to a Redis pub/sub channel. |

---

### 12. Configurable Deduplication Threshold

| | |
|---|---|
| **Document Says** | Different document types may need different sensitivity levels for deduplication. |
| **Code Has** | `NEAR_DUPLICATE_THRESHOLD = 0.8` is hardcoded in [dedupe_requirements.py:L31](file:///d:/ITI/GP/ai-pipeline/ai-service/app/nodes/dedupe_requirements.py#L31). |
| **Gap** | Cannot tune dedup sensitivity per-job. |
| **Implementation** | Add `dedup_threshold: float = 0.8` to `JobOptions` and pass it through pipeline state to `dedupe_requirements_node`. |

---

### 13. Rate Limiting on Public Endpoints

| | |
|---|---|
| **Document Says** | Section 11 discusses cost management and the need for _"per-job processing controls, max job runtime limits"_. |
| **Code Has** | `MAX_CONCURRENT_JOBS` limits in-process concurrency, but there is no per-IP or per-client rate limiting on `/process` or `/process-json`. |
| **Gap** | Public endpoints can be abused to trigger unlimited expensive LLM calls. |
| **Implementation** | Add `slowapi` middleware or a custom rate limiter for the public demo endpoints. |

---

### 14. CI/CD Pipeline (GitHub Actions)

| | |
|---|---|
| **Document Says** | _"GitHub Actions to run: Python tests, API contract tests, prompt snapshot tests, frontend lint/build, Docker image builds, and Alembic migration validations."_ (Section 11) |
| **Code Has** | The `.github/` directory contains only a `CODEOWNERS` file. There are **no** GitHub Actions workflow files (`.github/workflows/*.yml`). |
| **Gap** | Entire CI/CD pipeline is unimplemented. |
| **Implementation** | Create `.github/workflows/ci.yml` with jobs for: `poetry run pytest`, prompt snapshot tests, `alembic upgrade head --check`, Docker build, and linting. |

---

### 15. PII Masking Enhancement (Beyond Email/Phone)

| | |
|---|---|
| **Document Says** | Q9 flags: _"There is no PII scrubbing or data sanitization (e.g., masking API keys, emails, or personal names) prior to sending document contents to LLM endpoints."_ Section 11 mentions _"PII redaction and retention controls."_ |
| **Code Has** | [ingest.py:L92-96](file:///d:/ITI/GP/ai-pipeline/ai-service/app/nodes/ingest.py#L92-L96) does mask emails (`[EMAIL]`) and phone numbers (`[PHONE]`), which partially contradicts the document's claim. However, **personal names, addresses, API keys, credit card numbers, and other PII categories** are not detected or masked. |
| **Gap** | PII masking is partial (email/phone only). Names, addresses, SSNs, API keys, and credit card numbers pass through to LLM endpoints unmasked. |
| **Implementation** | Extend `_mask_pii()` with regex patterns for credit card numbers, SSN-like patterns, and API key formats. Optionally integrate a lightweight NER model for name/address detection, or use a configurable allowlist/blocklist approach. |

---

## 🟢 Nice-to-Have — Polish & Future Roadmap

These are explicitly marked as post-MVP in the document or represent polish items.

---

### 16. Vector Index Automation in Migrations

| | |
|---|---|
| **Document Says** | Q2 flags: _"Automatic vector index creation (e.g. HNSW indexes for speed) is not automated in Alembic migrations."_ |
| **Code Has** | Alembic migrations create the tables and columns but do not include `CREATE INDEX ... USING hnsw` or `ivfflat` statements. |
| **Implementation** | Add an Alembic migration that creates the vector index automatically. |

---

### 17. Job History / Listing Endpoint

| | |
|---|---|
| **Document Says** | Implied by the multi-tenant architecture and the existence of DB indexes on `(tenant_id, project_id, created_at)`. |
| **Code Has** | No `GET /internal/jobs` list endpoint. The backend must know the `job_id` beforehand. |
| **Implementation** | Add paginated `GET /internal/jobs?tenant_id=X&project_id=Y&status=COMPLETED&limit=20`. |

---

### 18. Cleanup / Retention Cron

| | |
|---|---|
| **Document Says** | Section 11 mentions _"retention controls"_. Config vars `JOB_RESULT_RETENTION_DAYS` and `CHUNK_RETENTION_DAYS` exist. |
| **Code Has** | `PgJobStore.cleanup_expired` method exists but **nothing calls it** on a schedule. |
| **Implementation** | Add a periodic cleanup command (`python -m app.worker.cleanup`) or integrate into the RQ worker as a scheduled task. |

---

### 19. Structured JSON Logging

| | |
|---|---|
| **Document Says** | Section 11 mentions observability and production-grade diagnostics. |
| **Code Has** | Standard Python text logging (`logging.info(...)`) throughout. |
| **Implementation** | Add a JSON formatter activated when `ENV=production`. |

---

### 20. OpenAPI Schema Enrichment

| | |
|---|---|
| **Document Says** | Section 8 describes a clear API layer. |
| **Code Has** | FastAPI endpoints lack detailed `response_model`, `summary`, and `responses` parameters. |
| **Implementation** | Enrich all endpoint decorators with proper OpenAPI metadata. |

---

### 21. Direct Jira Push (Post-MVP)

| | |
|---|---|
| **Document Says** | _"Direct Jira Cloud integration will be implemented post-MVP using Atlassian OAuth."_ (Section 10) |
| **Code Has** | Not implemented. Jira export is structured rows only. |
| **Implementation** | Future: Atlassian OAuth 2.0 + REST API integration. |

---

### 22. Direct Confluence Push (Post-MVP)

| | |
|---|---|
| **Document Says** | _"Direct Confluence integration is planned post-MVP to automate documentation generation."_ (Section 10) |
| **Code Has** | Not implemented. |
| **Implementation** | Future: Confluence Storage Format + REST API. |

---

## Summary Table

| # | Feature | Priority | Status |
|---|---------|----------|--------|
| 1 | Semantic Conflict Detection | 🔴 High | ❌ Not implemented |
| 2 | Human-in-the-Loop Review Workflow | 🔴 High | ❌ Not implemented |
| 3 | Self-Correction Loops | 🔴 High | ❌ Not implemented |
| 4 | Dynamic LLM Provider Fallback | 🔴 High | ❌ Not implemented |
| 5 | Multi-Document Awareness | 🔴 High | ❌ Not implemented |
| 6 | Callback Retry w/ Backoff | 🔴 High | ❌ Not implemented |
| 7 | CSV Export | 🔴 High | ❌ Not implemented |
| 8 | Langfuse Observability | 🟡 Medium | ❌ Not implemented |
| 9 | Semantic Section-Aware Chunking | 🟡 Medium | ❌ Not implemented |
| 10 | Language Detection | 🟡 Medium | ❌ Not implemented |
| 11 | Real-Time Progress (SSE) | 🟡 Medium | ❌ Not implemented |
| 12 | Configurable Dedup Threshold | 🟡 Medium | ❌ Not implemented |
| 13 | Rate Limiting | 🟡 Medium | ❌ Not implemented |
| 14 | CI/CD Pipeline (GitHub Actions) | 🟡 Medium | ❌ Not implemented |
| 15 | Enhanced PII Masking | 🟡 Medium | ⚠️ Partial (email/phone only) |
| 16 | Vector Index Automation | 🟢 Nice | ❌ Not implemented |
| 17 | Job Listing Endpoint | 🟢 Nice | ❌ Not implemented |
| 18 | Cleanup/Retention Cron | 🟢 Nice | ❌ Not implemented |
| 19 | Structured JSON Logging | 🟢 Nice | ❌ Not implemented |
| 20 | OpenAPI Schema Enrichment | 🟢 Nice | ❌ Not implemented |
| 21 | Direct Jira Push | 🟢 Nice | ❌ Post-MVP |
| 22 | Direct Confluence Push | 🟢 Nice | ❌ Post-MVP |
