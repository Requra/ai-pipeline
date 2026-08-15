# System Architecture

Purpose: Describe the runtime components and architectural boundaries that surround the AI pipeline. Audience: Backend engineers, platform engineers, and technical reviewers.

## Runtime topology

```mermaid
flowchart LR
    Caller["Backend or Local Caller"] --> API["FastAPI API\napp.main + /internal routes"]
    API --> JobStore[("Job Store\nMemory or PostgreSQL")]
    API --> Queue["Queue\nIn-Process or Redis/RQ"]
    Queue --> Worker["Worker Process\napp.worker.main"]
    Worker --> Recover["Input Recovery\nRedis Cache or Backend Client"]
    Recover --> Graph["Compiled LangGraph\n13 Pipeline Nodes"]
    Graph --> Provider["LLM / STT / Embedding Providers"]
    Graph --> Store[("Chunks, Embeddings, Results\nMemory or PostgreSQL/pgvector")]
    Worker --> Callback["Allowlisted Backend Callback\nOptional, Best-Effort"]
    Caller --> Health["/health and /ready Probes"]
```

## Component boundaries

| Component | Runs as | Source of truth / boundary |
|---|---|---|
| FastAPI API | API process | Validates requests, fingerprints inputs, creates jobs, exposes status and results. It does not execute the production worker graph directly. |
| In-process queue | API process | Development/test fallback; runs `run_job_entry()` with a concurrency semaphore. Not cross-process durable. |
| Redis/RQ | Separate Redis and worker process | Dispatches jobs and temporarily caches inline input (6-hour TTL). It is not authoritative storage. |
| Worker | `python -m app.worker.main` | Reconstructs input, runs the 13-node graph, persists artifacts, updates status, and attempts callbacks. |
| Store bundle | API and worker | `app.store.factory` selects memory when `DATABASE_URL` is empty, PostgreSQL/pgvector with configurable connection pooling otherwise. |
| Backend client | Worker | Fetches backend-owned sources and posts terminal callbacks. Host, redirect, size, and checksum checks are enforced. |
| External providers | Network services | Chat: OpenRouter/OpenAI/Groq through `ResilientLLMClient`; STT: Groq/Deepgram with fallback; embeddings: OpenAI/OpenRouter. |

## Request-to-result sequence

```mermaid
sequenceDiagram
    autonumber
    participant B as Backend / Caller
    participant A as FastAPI API
    participant D as PostgreSQL (Durable Store)
    participant Q as Redis / RQ (Queue & Cache)
    participant W as RQ Worker
    participant P as Providers (LLM / STT)
    participant C as Backend Callback

    B->>A: POST /internal/jobs (Bearer Token)
    A->>D: Create or compare job fingerprint (Idempotency)
    A->>Q: Enqueue job ID & cache transient input (6h TTL)
    A-->>B: 202 QUEUED (or idempotent/conflict response)
    Q->>W: Dequeue job
    W->>D: Mark PROCESSING and record attempt
    W->>W: Recover input and invoke 13-node compiled graph
    W->>P: LLM / STT / embedding calls as enabled
    W->>D: Persist source chunks, embeddings, result, events
    W->>D: Mark terminal status (COMPLETED / PARTIAL / REJECTED)
    W->>C: Optional allowlisted callback (best-effort)
    B->>A: GET /internal/jobs/{job_id}/result (polling alternative)
    A->>D: Read durable job/result
    A-->>B: Status or JobResult payload
```

## Trust and ownership boundaries

- Backend-owned source bytes remain outside this repository except for transient request/cache handling and persisted parsed chunks.
- `/internal/*` is protected by `AI_INTERNAL_SERVICE_TOKEN`; demo routes are intentionally compatibility/dev routes and are not equivalent to the production contract.
- Tenant and project identifiers are carried into jobs, chunks, embeddings, and results. PostgreSQL vector search applies those filters; missing identifiers remain an integration risk and must be supplied by the backend.
- Provider calls leave the service boundary with prompt/context data. Raw LLM I/O logging is disabled in production by `Settings.debug_llm_io_enabled`.
- The worker callback sends the result only when the URL matches the configured backend origin; failed callback delivery does not fail an already-persisted job.

## Runtime modes

| Mode | `DATABASE_URL` | `REDIS_URL` | Use |
|---|---|---|---|
| Tests/local minimal | empty | empty | In-memory stores and in-process execution. |
| Compose/production-shaped | set | set | PostgreSQL/pgvector durability and Redis/RQ separation. |
| Partial infrastructure | one set | the other empty | Supported by factories, but the durability/dispatch guarantees differ; validate readiness before relying on it. |

For security and exact configuration, see [09-security-and-configuration.md](09-security-and-configuration.md). For the graph itself, see [04-ai-pipeline.md](04-ai-pipeline.md).
