# Endpoint Code Interactions & Flow

This document details the code-level implementation of the FastAPI endpoints, the step-by-step functionality of each handler, and how the files in the repository interact to orchestrate the request and background processing flows.

---

## 🗺️ Codebase File Map & Roles

When a request enters the application, execution travels through several layers. Below is a list of the core files involved in the request lifecycle and their specific responsibilities:

```text
FastAPI Entry Point (main.py)
   │
   ├── Auth Dependencies (api/deps.py)
   │
   ├── Request Schemas (api/schemas.py)
   │
   └── Service Orchestration (api/service.py)
          │
          ├── Fingerprint Service (services/fingerprint.py)
          │
          ├── DB Repositories (store/db/repositories.py) ──> DB Models (store/models.py)
          │
          └── Queue Dispatcher (queue/redis_queue.py)
                 │
                 └── [Redis Queue Boundary]
                        │
                        └── RQ Worker Entry (worker/main.py)
                               │
                               └── Job Execution (worker/runner.py)
                                      │
                                      ├── State Ingest (worker/state.py) ──> S3 Fetch (clients/backend.py)
                                      │
                                      ├── Compiled Graph (graph/pipeline.py)
                                      │
                                      ├── Serializer (nodes/format.py) ──> Output Contracts (schemas/items.py)
                                      │
                                      └── Callback (clients/backend.py)
```

| Layer | File / Module | Responsibility |
| :--- | :--- | :--- |
| **API Entry** | [main.py](../ai-service/app/main.py) | Bootstraps FastAPI, mounts global middleware, registers operational endpoints (`/health`, `/ready`), and registers public demo routes. |
| **S2S Routes** | [internal.py](../ai-service/app/api/internal.py) | Registers all `/internal/*` routes and injects authentication/tracing dependencies. |
| **Auth Injector** | [deps.py](../ai-service/app/api/deps.py) | Handles token verification (`verify_internal_token`) and request tracing identifier extraction. |
| **Service Layer** | [service.py](../ai-service/app/api/service.py) | Coordinates high-level business rules: performs DB lookups, checks duplicate fingerprints, builds database rows, and calls the queue. |
| **Fingerprint** | [fingerprint.py](../ai-service/app/services/fingerprint.py) | Computes a SHA256 request payload hash to prevent duplicate processing of identical requests. |
| **Queue** | [redis_queue.py](../ai-service/app/queue/redis_queue.py) | Wraps the Redis/RQ client to enqueue job IDs and handles caching raw text/transcripts. |
| **Worker Daemon** | [worker/main.py](../ai-service/app/worker/main.py) | Runs as a separate process; listens to Redis/RQ and triggers execution upon receiving a job. |
| **Worker Runner** | [worker/runner.py](../ai-service/app/worker/runner.py) | Manages background job execution: marks DB processing status, catches exceptions, and executes webhooks on completion. |
| **State Builder** | [worker/state.py](../ai-service/app/worker/state.py) | Reconstructs the initial pipeline state, fetching source files via S3 or the backend client if Redis cache expires. |
| **Graph Router** | [graph/pipeline.py](../ai-service/app/graph/pipeline.py) | Defines `build_pipeline()`, compiles the 15-node LangGraph workflow, and registers conditional routes. |
| **DB Repository** | [repositories.py](../ai-service/app/store/db/repositories.py) | Encapsulates SQL transactions (SQLAlchemy) against Neon PostgreSQL (represented by [models.py](../ai-service/app/store/models.py)). |

---

## 🔌 1. API Endpoint Code Details

### Operational Endpoints ([app/main.py](../ai-service/app/main.py))

*   **Liveness Check (`GET /health`)**
    *   *Code Function*: `health_check()`
    *   *Details*: Instantly returns a static JSON payload `{"status": "healthy"}`. Bypasses database and network IO to prevent false-negative liveness checks.
*   **Readiness Check (`GET /ready`)**
    *   *Code Function*: `readiness_check()`
    *   *Details*: Calls `build_readiness_report()` from [app/startup.py](../ai-service/app/startup.py). Pings the PostgreSQL connection and Redis connection. If any core connection is broken, it raises an HTTP `503 Service Unavailable`.

### Public Demo Endpoints ([app/main.py](../ai-service/app/main.py))

*   **Submit Demo JSON (`POST /process-json`)**
    *   *Code Function*: `process_json()`
    *   *Details*: Validates input text. Calls `_create_and_dispatch_demo_job()`, which checks the process-local status cache and dispatches the job.
*   **Upload Demo Document (`POST /process`)**
    *   *Code Function*: `process_document()`
    *   *Details*: Receives file bytes via `multipart/form-data`. Calls `detect_mime_and_type()` to inspect file headers, enforces the 50MB size limit, and enqueues the job.
*   **Get Demo Status (`GET /status/{job_id}`)**
    *   *Code Function*: `get_job_status()`
    *   *Details*: Queries the local progress store (or PostgreSQL if enabled) and returns a mapped status payload.

### Internal S2S Production Endpoints ([app/api/internal.py](../ai-service/app/api/internal.py))

All endpoints below depend on the `require_internal_auth` dependency.

*   **Create Production Job (`POST /internal/jobs`)**
    *   *Code Function*: `create_job()`
    *   *Payload*: `CreateJobRequest` (Pydantic schema in [app/api/schemas.py](../ai-service/app/api/schemas.py)).
    *   *Logic Flow*:
        1.  Calls `handle_job_creation()` in `app/api/service.py`.
        2.  Calculates request fingerprint in `app/services/fingerprint.py`.
        3.  Queries `ai_jobs` table by `job_id` and fingerprint.
            *   *Scenario A (New Job)*: Writes job row in `ai_jobs` with status `QUEUED`. Funnels into `prepare_and_dispatch_job()`, saving source document records in the database, caching inputs in Redis, and placing the job ID on the RQ queue. Returns `202 Accepted`.
            *   *Scenario B (Idempotent Match)*: If the same `job_id` and fingerprint exist, it returns the current status and links without reprocessing.
            *   *Scenario C (Conflict)*: If `job_id` exists but the fingerprint differs (content was changed), raises `409 Conflict`.
*   **Get Job Status (`GET /internal/jobs/{job_id}`)**
    *   *Code Function*: `get_job()`
    *   *Details*: Queries the `ai_jobs` table. Returns `200 OK` with database status fields (`progress_pct`, `current_node`, `warning_count`).
*   **Get Final Result (`GET /internal/jobs/{job_id}/result`)**
    *   *Code Function*: `get_job_result()`
    *   *Details*: Queries the `ai_job_results` table.
        *   If result exists, returns the full serialized `JobResult` payload.
        *   If the job is still processing, returns `409 Conflict` containing the current job status.
*   **Cancel Job (`POST /internal/jobs/{job_id}/cancel`)**
    *   *Code Function*: `cancel_job()`
    *   *Details*: Updates `cancel_requested = True` in the database. The background worker checks this flag cooperatively between graph node boundaries and halts if set.
*   **Retry Job (`POST /internal/jobs/{job_id}/retry`)**
    *   *Code Function*: `retry_job()`
    *   *Details*: Evaluates if the current job status is `FAILED` or `CANCELLED`. If so, increments the database `attempt_number`, resets status to `QUEUED`, and enqueues the job back to the RQ worker queue.

---

## Production endpoint and file-transport guide

This section is the canonical explanation of how a backend integration supplies
text, uploaded bytes, or backend-owned document references. The endpoint schema
accepts `file_url` as optional, but document and audio jobs still need a
retrievable source at execution time: either an explicit `file_url`, or a
`document_id` together with a configured `BACKEND_BASE_URL`.

### Which production endpoint supports which input

| Endpoint | Auth | Accepts binary upload | Accepts inline text | Supports backend document references | Downloads from `file_url` | Typical use |
|---|---|---:|---:|---:|---:|---|
| `POST /internal/jobs` | Internal bearer token | No | Yes, for `text` and `backend_transcript` | Yes, for `backend_document` and `backend_audio` | Yes | Canonical production job API. |
| `POST /internal/process` | Internal bearer token | Yes, multipart | No | Creates a source reference for the uploaded file | Not required for the submitted file | Compatibility upload endpoint. |
| `POST /internal/process-json` | Internal bearer token | No | Yes | Optional metadata only; content is required | Only used if the worker must recover missing transcript text | Compatibility text/transcript endpoint. |
| `GET /internal/documents/{document_id}/content` | Internal bearer token | Response only | No | Retrieves content from local/cache/database/backend | Backend-side source recovery | Source recovery and diagnostics. |

The internal routes are mounted in `app/api/internal.py`; request models are in
`app/api/schemas.py`.

### Option A: backend-owned storage with document references

This is the recommended production integration for documents and audio. The
backend keeps ownership of the original file and sends metadata rather than
copying the file through the job-creation request.

```json
{
  "job_id": "job-001",
  "tenant_id": "tenant-1",
  "project_id": "project-1",
  "input_type": "backend_document",
  "source_documents": [
    {
      "document_id": "document-123",
      "file_type": "pdf",
      "mime_type": "application/pdf",
      "sha256_hash": "<optional-sha256>"
    }
  ]
}
```

The `file_url` may be omitted when the AI service has:

```text
BACKEND_BASE_URL=https://backend.example.com
BACKEND_SERVICE_TOKEN=<service token>
```

The worker then requests:

```text
GET https://backend.example.com/internal/documents/document-123/content
```

The current implementation is:

```text
app/worker/state.py → build_worker_initial_state()
  → app/clients/backend.py → BackendDocumentClient.fetch_document_bytes()
  → detect_file_type
  → ingest
```

For text/transcript references, `fetch_document_text()` uses the supplied
`file_url` or falls back to:

```text
GET {BACKEND_BASE_URL}/internal/documents/{document_id}/text
```

`document_id` alone is not sufficient when `BACKEND_BASE_URL` is unset.

### Option B: explicit presigned `file_url`

The backend may provide a short-lived object-storage URL:

```json
{
  "job_id": "job-002",
  "project_id": "project-1",
  "input_type": "backend_document",
  "source_documents": [
    {
      "document_id": "document-456",
      "file_type": "pdf",
      "mime_type": "application/pdf",
      "file_url": "https://storage.example.com/signed/document-456.pdf",
      "sha256_hash": "<sha256>"
    }
  ]
}
```

`BackendDocumentClient.fetch_document_bytes()` validates the URL, blocks
unsafe hosts and credentials in URLs, limits size, handles redirects with
additional checks, and verifies the hash when one is supplied. Custom storage
domains must be included in `ALLOWED_DOWNLOAD_DOMAINS` unless they match the
configured backend origin.

Use short-lived URLs only. The source manifest stores `file_url`, so long-lived
URLs or URLs containing credentials should not be sent.

`storage_key` is stored as source metadata but is not itself converted into a
download URL by the current client. Supply either `file_url`, or a
`document_id` resolvable through `BACKEND_BASE_URL`.

### Option C: multipart upload to the AI service

`POST /internal/process` accepts the file bytes directly:

```text
multipart/form-data
  file       = requirements.pdf
  job_id     = upload-001
  project_id = project-1
  tenant_id  = tenant-1
  document_id = document-789 (optional but recommended)
```

The current server-side path is:

```text
app/api/internal.py → await file.read()
  → prepare_and_dispatch_job(raw_bytes=file_bytes)
  → initial PipelineState.raw_bytes
  → Redis transient input cache when Redis is enabled
  → worker
  → detect_file_type
  → ingest
```

The endpoint also creates a source-document manifest, but the AI persistence
layer stores derived chunks/results rather than durable original file bytes.
The submitted bytes are held in process memory or the transient Redis input
cache, so this option is best for smaller files, controlled deployments, and
local development.

### Option D: inline text or already-transcribed meetings

For text:

```json
{
  "job_id": "text-001",
  "project_id": "project-1",
  "input_type": "text",
  "content": "The system shall ..."
}
```

For an already-transcribed meeting, use `backend_transcript` with `content`.
No file URL is required. The content is placed in `raw_text` and passes
through file detection and ingest as text. Optional `source_documents` can be
included for provenance, but the original audio is not downloaded by this
path.

### Where the original file exists during processing

| Integration | Initial location | Worker recovery | Durable original bytes? |
|---|---|---|---:|
| `/internal/process` | AI API process memory | Initial state, or Redis cache | No |
| `/internal/jobs` with `file_url` | Backend/object storage | Direct download by worker | No |
| `/internal/jobs` with document ID | Backend storage | Backend content endpoint | No |
| `/internal/jobs` with inline text | Request/Redis cache | Initial state or Redis cache | No |
| `/internal/process-json` transcript | Request/Redis cache | Initial state or Redis cache | No |

The AI service durably stores the source manifest, chunks, embeddings when
enabled, generated result, and job metadata. Raw source ownership remains with
the backend or object-storage system.

### Recommended decision

Use this order for production integrations:

1. `backend_document` or `backend_audio` with `document_id` and a protected
   backend content endpoint.
2. Presigned `file_url` for large-file/high-throughput deployments where direct
   object-storage download is preferred.
3. `/internal/process` multipart upload for small files or local development.
4. `text` or `backend_transcript` when the backend already owns the extracted
   or transcribed text.

Do not send a document job with only `document_id` unless
`BACKEND_BASE_URL` is configured. It will be accepted by schema validation but
can fail later during worker source recovery.

---

## 🔗 2. Core Code Interactions: The End-to-End Flow

To trace how these code files interact during a job's lifecycle:

### Phase A: Request Processing & Enqueuing (Synchronous)

```text
[HTTP Client]
      │ (POST /internal/jobs)
      ▼
[app/api/internal.py::create_job]
      │
      ├──> Calls [app/api/deps.py::require_internal_auth] (verify service token)
      │
      ├──> Validates schema with [app/api/schemas.py::CreateJobRequest]
      │
      └──> Calls [app/api/service.py::handle_job_creation]
                 │
                 ├──> Calls [app/services/fingerprint.py::compute_fingerprint]
                 │
                 ├──> Calls [app/store/db/repositories.py::get_job] (queries DB)
                 │
                 └──> Calls [app/api/service.py::prepare_and_dispatch_job]
                            │
                            ├──> Calls [app/store/db/repositories.py::create_job_and_documents]
                            │
                            ├──> Calls [app/queue/redis_queue.py::enqueue_job]
                            │          │
                            │          └──> Writes inputs to Redis (6h cache)
                            │               Enqueues job ID to RQ
                            │
                            └──> Returns 202 Accepted JSON payload
```

---

### Phase B: Worker Execution (Asynchronous)

Once the worker daemon registers the enqueued job ID:

```text
[Redis Queue]
      │ (Triggers worker process)
      ▼
[app/worker/main.py] (worker daemon loop)
      │
      └──> Invokes [app/worker/runner.py::run_job_entry]
                 │
                 ├──> Calls [app/store/db/repositories.py::mark_processing] (updates DB status)
                 │
                 ├──> Calls [app/worker/state.py::build_worker_initial_state]
                 │          │
                 │          ├──> Reads Postgres Job & Documents config
                 │          │
                 │          └──> Recovers raw content (from Redis cache or S3 downloads)
                 │
                 ├──> Calls [app/graph/pipeline.py::build_pipeline] (compiles LangGraph)
                 │
                 ├──> Executes graph.ainvoke(initial_state)
                 │          │
                 │          └──> State moves through the 15 graph nodes:
                 │               - detect_file_type.py
                 │               - ingest.py
                 │               - transcribe.py (STT calls to Groq/Deepgram)
                 │               - parse_to_chunks.py
                 │               - build_source_index.py (persists pgvector embeddings)
                 │               - extract.py (LLM extraction via ResilientLLMClient)
                 │               - dedupe_requirements.py
                 │               - retrieve_evidence.py
                 │               - classify.py
                 │               - evidence_grounding.py
                 │               - generate.py (generates stories)
                 │               - quality_gate.py (validates quality)
                 │               - repair_stories.py (loops if needed)
                 │               - summarize.py (builds summary)
                 │               - format.py (serializes state to V1 format.py)
                 │
                 ├──> Calls [app/store/db/repositories.py::save_job_results]
                 │          (Persists requirements, stories, and summary to DB)
                 │
                 ├──> Calls [app/store/db/repositories.py::mark_completed] (sets database to terminal status)
                 │
                 └──> Calls [app/clients/backend.py::post_callback]
                            (Optionally posts JSON results to allowlisted backend webhook)
```
