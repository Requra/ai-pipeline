# Requra.AI — Production AI Pipeline Architecture

This document describes the production-ready AI processing service: how it is
wired, the endpoint and DB contracts, the queue/worker lifecycle, the RAG
strategy, security, and how to run it locally and in production.

The service keeps its **MVP output contract (`JobResult`, `contract_version="1.0"`)
unchanged**. Everything here is additive: durable storage, a real queue/worker,
service auth, idempotency, cancellation, retry, callbacks, and hybrid retrieval.

---

## 1. Architecture

```
Frontend
  │
  ▼
Backend API (.NET)  ── owns users, auth, projects, uploads, raw file/audio storage
  │  creates AI job (POST /internal/jobs)  ▲ receives callback / GET result
  ▼                                        │
┌──────────────────────── AI Processing Service ───────────────────────────┐
│  FastAPI (API)                Redis (queue + input cache)                  │
│    /internal/jobs  ──enqueue──►  ai_jobs queue  ──►  RQ Worker(s)          │
│    /process, /process-json                              │                  │
│    /status, /ready, /health                             ▼                  │
│                                              LangGraph pipeline (14 nodes) │
│                                                         │                  │
│   PostgreSQL + pgvector  ◄── persist ── jobs, events, attempts, source     │
│                               documents, chunks, embeddings, requirements, │
│                               stories, coverage, quality, warnings, result │
└────────────────────────────────────────────────────────────────────────┘
```

**Responsibility split**

| Backend owns | AI service owns |
| --- | --- |
| Users, auth, projects, meetings | AI job lifecycle + status |
| Uploaded files/audio + **raw storage**, storage keys, file URLs | Extract/transcribe/parse → chunks |
| Creating AI jobs; providing document refs or text | RAG retrieval data + embeddings |
| Receiving/fetching final result | Requirements, stories, summaries, exports, quality, evidence |
| Frontend-facing business APIs | AI job status, chunks, embeddings, outputs, warnings, errors |

The AI service **never** duplicates raw file storage. It reads backend-provided
text or document references and persists only *derived* data.

**Component map (code)**

| Concern | Module |
| --- | --- |
| Typed config + fail-fast | `app/config.py` |
| Store interfaces | `app/store/base.py` (`JobStore`, `ResultStore`, `ChunkStore`, `EmbeddingStore`) |
| In-memory stores (dev/test) | `app/store/memory.py` |
| Postgres + pgvector stores | `app/store/db/` (models, session, repositories) |
| Store selection | `app/store/factory.py` (`DATABASE_URL` → Postgres, else memory) |
| Queue | `app/queue/` (`InProcessQueue` default, `RedisQueue` prod) |
| Worker execution | `app/worker/runner.py` (`execute_job`), `app/worker/main.py` (RQ entry) |
| Backend client | `app/clients/backend.py` (doc fetch + callback) |
| Internal API | `app/api/internal.py`, auth/tracing in `app/api/deps.py` |
| Shared job service | `app/api/service.py` |
| Hybrid RAG | `app/rag/embeddings.py`, `app/rag/hybrid.py`, nodes `build_source_index`, `retrieve_evidence` |

---

## 2. Endpoint contract

### Public (demo/dev-compatible — unchanged)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Liveness. |
| GET | `/ready` | Readiness diagnostics (booleans/provider names only). 200/503. |
| GET | `/status/{job_id}` | Stable status shape (DB-backed in production). |
| POST | `/process` | multipart file (demo/dev). Returns `202 {job_id, status:"QUEUED"}`. |
| POST | `/process-json` | direct text (demo/dev). Returns `202 {job_id, status:"QUEUED"}`. |

`/process` and `/process-json` are **demo/dev-compatible**: they accept content
directly and hold it in memory for the run. Internally they create the same
durable job + dispatch through the same queue as production jobs.

### Internal production API (`Authorization: Bearer <AI_INTERNAL_SERVICE_TOKEN>`)

**`POST /internal/jobs`** — create/enqueue (idempotent by `job_id`).

```jsonc
{
  "job_id": "be-job-123",              // backend-provided, ^[A-Za-z0-9._-]{1,128}$
  "tenant_id": "tenant-1",
  "project_id": "project-9",
  "requested_by": "user-42",           // or "user_id"
  "input_type": "text",                // text | backend_document | backend_transcript | backend_audio
  "content": "The system must ...",     // required for text/backend_transcript
  "source_documents": [                 // required for backend_document/backend_audio
    {"document_id": "D-1", "file_type": "pdf", "mime_type": "application/pdf",
     "storage_key": "s3://...", "file_url": "https://...", "sha256_hash": "...", "page_count": 12}
  ],
  "options": {
    "generate_user_stories": true, "generate_summary": true,
    "enable_embeddings": false, "enable_hybrid_retrieval": false,
    "language": "en", "callback_url": "https://backend/callbacks/be-job-123",
    "priority": "normal"
  },
  "reprocess": false                    // true = reprocess an existing job as a new attempt
}
```

Response `202` (new job):
```json
{"job_id":"be-job-123","status":"QUEUED","attempt_number":1,"idempotent":false,
 "links":{"self":"/internal/jobs/be-job-123","result":".../result","cancel":".../cancel","retry":".../retry"}}
```

A repeated `job_id` is **not** blindly re-enqueued — see [§2a Idempotency and duplicate-job
handling](#2a-idempotency-and-duplicate-job-handling) for the full behavior matrix (running/
completed/failed/cancelled × same/different payload × `reprocess` flag).

**`GET /internal/jobs/{job_id}`** — durable status:
```json
{"job_id":"be-job-123","status":"COMPLETED","progress_pct":100,"current_node":"format",
 "attempt_number":1,"tenant_id":"tenant-1","project_id":"project-9","input_type":"text",
 "error_code":null,"warning_count":0,"quality_score":0.94,"links":{...}}
```

**`GET /internal/jobs/{job_id}/result`** — persisted `JobResult` (contract v1). `409` if not complete, `404` if unknown.

**`POST /internal/jobs/{job_id}/cancel`** — cooperative cancel. QUEUED → `CANCELLED` immediately; PROCESSING → flagged, worker stops at the next node boundary. Terminal → `{"cancelled": false}`.

**`POST /internal/jobs/{job_id}/retry`** — new attempt for a `FAILED`/`CANCELLED` job only
(`409` if still running or already terminal-success — completed jobs are never silently
reprocessed). Preserves attempt history; reconstructs input from the Redis cache → backend →
persisted chunks (no re-upload, no duplicated documents). The check-and-requeue is atomic
(row-locked), so two concurrent `/retry` calls for the same job can never both dispatch.

**`POST /internal/jobs/{job_id}/callback-test`** — guarded diagnostics (disabled in production).

### 2a. Idempotency and duplicate-job handling

`job_id` alone cannot tell whether a repeated `POST /internal/jobs` is a safe retry of the
*same* logical request or an accidental reuse of a `job_id` for a *different* request. Every
request is fingerprinted (`app/services/fingerprint.py`) and compared against the fingerprint
stored on the existing job:

**Request fingerprint** — SHA-256 of a canonical JSON object built from:
`tenant_id`, `project_id`, `requested_by`/`user_id`, `input_type`, a SHA-256 of normalized
inline content (CRLF→LF, outer whitespace trimmed, **case preserved**), the sorted, normalized
`source_documents` (`document_id`, `storage_key`/`file_url`, `sha256_hash`, `mime_type`,
`file_type`), and the pipeline-behavior-affecting options (`generate_user_stories`,
`generate_summary`, `enable_embeddings`, `enable_hybrid_retrieval`, `language`).
**Excluded** (never change the fingerprint): `job_id` itself, `callback_url`, `priority`, the
`reprocess` flag, `X-Request-Id`/tracing headers, and timestamps. Raw content is never stored
or logged — only its hash.

**Behavior matrix** for `POST /internal/jobs` with an existing `job_id`:

| Existing status | Same payload | Different payload | `reprocess=true` |
| --- | --- | --- | --- |
| QUEUED / PROCESSING (running) | `202` idempotent, no re-enqueue | `409 JOB_ID_CONFLICT` | `409 JOB_NOT_RETRYABLE` |
| COMPLETED / PARTIAL / REJECTED | `200` idempotent + result link | `409 JOB_ID_CONFLICT` | `409 JOB_NOT_RETRYABLE` |
| FAILED / CANCELLED | `200` (report only, no re-enqueue) | `409 JOB_ID_CONFLICT` | `202`, new attempt, one enqueue |
| Different tenant/project, same `job_id` | `409 JOB_ID_CONFLICT` (no detail leak) | same | same |

`reprocess=true` with a fingerprint mismatch is **always** `409` — retry requires the retried
payload to match what was originally submitted. Every repeated submission against an existing
`job_id` (matched or not) increments `ai_jobs.duplicate_request_count` / stamps
`last_duplicate_request_at`, and records a `DUPLICATE_REQUEST` (`INFO`) job event; a conflicting
one additionally records a `JOB_ID_CONFLICT` (`WARNING`) event. Event metadata is booleans and
status strings only — raw content is never logged.

**Concurrency safety** — two identical concurrent `POST /internal/jobs` (or two concurrent
`/retry` calls) for the same `job_id` race-safely produce exactly one dispatch:
  * `JobStore.create_or_get` — Postgres: single `INSERT ... ON CONFLICT (job_id) DO NOTHING`,
    then the loser re-reads the winner's row via `SELECT ... FOR UPDATE` inside the same
    transaction; in-memory: one lock-guarded critical section. Exactly one caller observes
    `created=True`.
  * `JobStore.try_requeue_for_retry` — the FAILED/CANCELLED→QUEUED transition (status check +
    fingerprint check + attempt bump) is one atomic check-and-set (row-locked in Postgres, the
    same in-memory lock otherwise); a losing concurrent retry observes the now-QUEUED status
    and safely no-ops instead of double-dispatching.

Response shapes:
```jsonc
// running + same payload -> 202
{"job_id":"ai_job_123","status":"PROCESSING","progress_pct":45,"current_node":"extract",
 "attempt_number":1,"idempotent":true,"duplicate_of":"ai_job_123",
 "message":"Job is already running; duplicate request was not enqueued."}

// running + different payload -> 409
{"error":{"code":"JOB_ID_CONFLICT","message":"...","job_id":"ai_job_123",
 "existing_status":"PROCESSING","hint":"Use a new job_id, or cancel/retry ..."}}

// completed + same payload -> 200
{"job_id":"ai_job_123","status":"COMPLETED","idempotent":true,"duplicate_of":"ai_job_123",
 "result_available":true,"links":{"result":"/internal/jobs/ai_job_123/result"}}

// failed + same payload + reprocess=true -> 202, new attempt
{"job_id":"ai_job_123","status":"QUEUED","attempt_number":2,"retried":true,
 "message":"Failed/cancelled job was queued for retry."}
```

### Status vocabulary

Durable statuses: `QUEUED, PROCESSING, COMPLETED, FAILED, CANCELLED, PARTIAL, REJECTED`.
The **public** `/status` maps `PARTIAL`/`REJECTED` → `COMPLETED` for backward
compatibility (the nuance lives in `result.status` = `completed|partial|failed|rejected`).

---

## 3. Backend ↔ AI service flow

1. Backend saves the raw file/audio (its storage) and extracts/transcribes text if it wants.
2. Backend `POST /internal/jobs` with either inline `content` or `source_documents` refs.
3. AI service validates + authenticates, enforces idempotency by `job_id`, persists the `ai_jobs` row, enqueues.
4. A worker runs the LangGraph pipeline, persisting chunks/requirements/stories/quality/result.
5. On completion the AI service `POST`s the result to `options.callback_url` (if set); the backend may also poll `GET /internal/jobs/{id}/result`.
6. Frontend displays the result the backend surfaces.

For `backend_document`/`backend_audio`, the worker fetches extracted/transcribed
**text** via `BackendDocumentClient` (`file_url`, else `GET {BACKEND_BASE_URL}/internal/documents/{id}/text`). It never pulls raw bytes.

---

## 4. Database schema (PostgreSQL + pgvector)

ORM: `app/store/db/models.py`; migrations: `ai-service/migrations/`
(`0001_initial` — baseline schema; `0002_job_idempotency` — adds `request_fingerprint`,
`request_fingerprint_version`, `idempotency_key`, `last_duplicate_request_at`,
`duplicate_request_count` to `ai_jobs` + an index on `request_fingerprint` and a composite
`(tenant_id, project_id, job_id)` index).

| Table | Purpose |
| --- | --- |
| `ai_jobs` | job lifecycle: status, current_node, progress, attempt_number, options_json, error, callback_url, cancel_requested, timestamps, request fingerprint + duplicate-request counters |
| `ai_job_events` | per-node/event audit trail (type, node, severity, metadata) |
| `ai_job_attempts` | attempt history (attempt_number, status, timings, error) |
| `ai_source_documents` | backend doc references + metadata (no raw bytes) |
| `ai_source_chunks` | parsed/transcribed chunks (text, page/speaker/time spans, offsets) |
| `ai_source_chunk_embeddings` | `vector(EMBEDDING_DIMENSIONS)` embeddings (pgvector) |
| `ai_requirements` + `ai_requirement_evidence` | extracted requirements + evidence quotes |
| `ai_user_stories` + `ai_acceptance_criteria` | generated stories + ACs |
| `ai_requirement_coverages` | requirement→story/AC coverage mapping |
| `ai_quality_reports` + `ai_quality_issues` | aggregate scores + per-item issues |
| `ai_pipeline_warnings` | node warnings |
| `ai_job_results` | full `JobResult` JSON (source of truth for `get_result`) + exports/artifacts |

**Indexes**: `job_id` PK/unique where appropriate, `(tenant_id, project_id, created_at)`,
`(status)`, `ai_source_chunks(job_id)` / `(project_id)`, dedupe keys, `backend_document_id`,
and an **IVFFLAT cosine index** on the embedding column.

`ResultStore.save_result` writes the full JSON **and** decomposes it into the
normalized tables for queryability; the JSON remains authoritative.

---

## 5. Queue & worker lifecycle

**Queue choice: RQ** (Redis Queue). Rationale: Redis-only (matches the
"Redis for queue/dispatch/cache only" constraint — no extra broker/result
backend); jobs are coarse-grained (one graph run) and durable state already
lives in Postgres, so we only need reliable at-least-once *dispatch*; a worker is
`python -m app.worker.main`. Celery/Dramatiq were heavier for this shape.

- **In-process** (no `REDIS_URL`): jobs run in the API process via FastAPI
  BackgroundTasks, bounded by `MAX_CONCURRENT_JOBS`. Default for dev/demo/tests.
- **Redis/RQ** (`REDIS_URL` set): the API caches transient input in Redis and
  enqueues by `job_id`; a separate worker fleet reconstructs state and runs.

**`execute_job` (worker/runner.py)**
1. Load `ai_job`, record attempt, set `PROCESSING`.
2. Run the pipeline — **streamed node-by-node** in the worker so progress and
   cancellation are observed *between* nodes (single `ainvoke` on the in-process path).
3. Persist major artifacts as nodes complete (chunks after parse/transcribe) and
   the final `JobResult` after `format`.
4. Map terminal state → `COMPLETED | PARTIAL | REJECTED | FAILED | CANCELLED`.
5. Fire the backend callback if `callback_url` is set.

**Cancellation** is cooperative: the API sets `cancel_requested`; the worker
checks it between nodes and stops safely (`CANCELLED`).
**Retry** increments `attempt_number`, preserves history, and reconstructs input
without duplicating source documents.
**Timeouts**: `MAX_JOB_RUNTIME_SECONDS` (whole job), `PROVIDER_TIMEOUT_SECONDS`
(provider calls), RQ `job_timeout`.

Multiple API and worker instances run safely: Postgres is the shared source of
truth and Redis is the shared dispatch — no process-local state is authoritative
in production. The in-memory per-job BM25 index is cleared after each job.

---

## 6. RAG strategy

RAG here is for **source grounding + traceability**, never chat.

- **BM25 lexical** (`app/rag/`): per-job in-memory index over the job's chunks —
  authoritative for exact/verbatim grounding. Always on.
- **pgvector semantic** (opt-in via `enable_embeddings`): `build_source_index`
  embeds chunks and persists them scoped by `tenant_id/project_id/job_id`.
- **Hybrid** (opt-in via `enable_hybrid_retrieval`): in `retrieve_evidence`, per
  requirement we run BM25 **and** a vector search (scoped by tenant/project/job),
  then `merge_hits` ranks them (agreement between signals is boosted). Vector
  hits carry the chunk's *own* text, so an attached snippet is always real source
  text — vector retrieval can never invent unsupported evidence.
- **Quote verification stays**: `quote_support_score`, `evidence_match_score`, and
  the new `vector_match_score` are recorded; `evidence_grounding` still validates.
- **Isolation**: vector search is always tenant/project scoped, so semantic recall
  never crosses tenants or projects (covered by tests).

The MVP default is lexical-only (embeddings disabled), so behavior is unchanged
unless a job opts in.

---

## 7. Security

- `/internal/*` requires `Authorization: Bearer <AI_INTERNAL_SERVICE_TOKEN>`
  (constant-time compare; `401` missing/malformed, `403` wrong). Missing token →
  `503` in production, allowed in dev with a warning.
- **Tracing**: every request gets an `X-Request-Id` (accepted or generated) echoed
  back; the access log records method/path/status/duration/request_id — **never**
  bodies, query values, or headers.
- **PII/secrets**: raw document text, full prompts, and full LLM responses are
  never logged in production (`DEBUG_LLM_IO` is force-disabled when `ENV=production`).
  `/ready` reports only booleans + provider names. Job ids are sanitized.
- Only necessary chunk/evidence text is stored; retention is bounded by
  `JOB_RESULT_RETENTION_DAYS` / `CHUNK_RETENTION_DAYS` (store `cleanup_expired`).

---

## 8. Configuration (env vars)

See `ai-service/.env.example`. Key vars: `ENV`, `ALLOWED_ORIGINS`,
`AI_INTERNAL_SERVICE_TOKEN`, `DATABASE_URL`, `REDIS_URL`, `QUEUE_NAME`,
`JOB_RESULT_RETENTION_DAYS`, `CHUNK_RETENTION_DAYS`, `ENABLE_EMBEDDINGS`,
`EMBEDDING_PROVIDER/MODEL/DIMENSIONS`, `ENABLE_HYBRID_RETRIEVAL`, `LLM_PROVIDER`,
`OPENROUTER_API_KEY`/`OPENAI_API_KEY`/`GROQ_API_KEY`, `DEEPGRAM_API_KEY`,
`TRANSCRIBE_PROVIDER`, `ENABLE_AUDIO`, `BACKEND_BASE_URL`, `BACKEND_SERVICE_TOKEN`,
`CALLBACK_TIMEOUT_SECONDS`, `MAX_JOB_RUNTIME_SECONDS`, `MAX_CONCURRENT_JOBS`,
`DEBUG_LLM_IO`.

**Production fails fast** (`app.startup.run_startup_checks`) if: LLM provider/key
missing, `AI_INTERNAL_SERVICE_TOKEN` unset, `ALLOWED_ORIGINS` empty, or
`DATABASE_URL` unset. `/ready` returns 503 until DB + Redis (if configured) are
reachable and required providers/tokens are present.

---

## 9. Local development

```bash
# Full production-shaped stack (Postgres + pgvector, Redis, migrate, API, worker)
cp ai-service/.env.example ai-service/.env      # fill in an LLM key + token
docker compose up --build
# API on http://localhost:8000  (migrations run automatically via the `migrate` service)

# --- OR run pieces by hand ---
cd ai-service
poetry install
# migrations (needs DATABASE_URL):
DATABASE_URL=postgresql+asyncpg://ai:ai@localhost:5432/ai_pipeline poetry run alembic upgrade head
# API:
poetry run uvicorn app.main:app --reload --port 8000
# worker (needs REDIS_URL):
poetry run python -m app.worker.main

# Tests + MVP eval (no infra required — in-memory backend):
poetry run pytest -q
poetry run python scripts/evaluate_pipeline.py
```

Without `DATABASE_URL`/`REDIS_URL` the service runs entirely in-memory/in-process
— ideal for tests and quick local demos.

---

## 10. Deployment

1. Provision PostgreSQL with the `vector` extension and a Redis instance.
2. Set env (see §8) — `ENV=production`, real `AI_INTERNAL_SERVICE_TOKEN`,
   `ALLOWED_ORIGINS`, `DATABASE_URL`, `REDIS_URL`.
3. Run migrations: `alembic upgrade head`.
4. Deploy the **API** (`uvicorn app.main:app`) and one or more **workers**
   (`python -m app.worker.main`) — scale independently.
5. Gate traffic on `GET /ready` (503 until dependencies are healthy).

---

## 11. Migration guide (from the direct-demo endpoints)

- Existing callers of `/process`, `/process-json`, `/status/{job_id}` keep working
  unchanged — same request/response shapes, same `JobResult`.
- New backend integrations should use `POST /internal/jobs` with a service token,
  and either poll `GET /internal/jobs/{id}/result` or receive the `callback_url`.
- Move raw file ownership to the backend; pass `content` (text) or
  `source_documents` refs to the AI service.
- Turn on durability by setting `DATABASE_URL`; turn on the worker fleet by setting
  `REDIS_URL`. No code changes required to switch backends — the store/queue
  factories select by config.

---

## 12. Known limitations / future work

- Postgres/Redis/RQ paths are implemented and unit-tested via the shared
  protocols, but end-to-end verification against a live Postgres+pgvector and a
  running RQ worker should be run in CI/staging (not exercised in the offline test
  environment here). `alembic upgrade head` + an integration test are the gates.
- Embeddings/hybrid retrieval are opt-in and default off (MVP stays lexical).
- The in-memory backend is single-process and non-durable (dev/test only).
- Audio jobs assume the backend provides audio or a transcript; the AI service
  does not store raw audio.
- Callback delivery is best-effort (logged + recorded as a job event); a durable
  outbox/retry for callbacks is a future enhancement.
- The `INSERT ... ON CONFLICT DO NOTHING` + `SELECT ... FOR UPDATE` idempotent-create
  and the row-locked retry transition in `PgJobStore` are implemented against the same
  `JobStore` protocol the in-memory backend satisfies (and are unit-tested there under
  real `asyncio.gather` concurrency), but — like the rest of the Postgres path — have not
  been exercised against a live database with genuinely concurrent connections in this
  offline environment; verify under load in staging before relying on it at scale.
- The `idempotency_key` column is reserved for callers that want to key idempotency on
  something other than `job_id`; it is not yet read/written by any endpoint.
