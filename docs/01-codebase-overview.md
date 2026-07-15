# Codebase overview

Purpose: explain the repository structure, ownership boundaries, and important entry points. Audience: anyone onboarding to the AI service or tracing a feature.

## What this repository contains

Requra.AI is a Python 3.11 AI-processing microservice. This repository does not contain the product frontend, the .NET/backend domain service, or the backend's object storage. Those systems call this service through the internal API and consume its structured result.

The service accepts inline text/transcripts and backend-owned document/audio references. It validates and normalizes the input, runs a LangGraph workflow, stores job artifacts, and exposes status/result APIs.

## Repository map

```text
ai-pipeline/
├── ai-service/
│   ├── app/
│   │   ├── api/              HTTP schemas, auth, job orchestration
│   │   ├── clients/          backend document fetch and callback client
│   │   ├── graph/            LangGraph construction and routers
│   │   ├── nodes/            15 pipeline stages
│   │   ├── prompts/          registry, loader, and runtime templates
│   │   ├── rag/              BM25, embeddings, vector/hybrid retrieval
│   │   ├── store/            storage protocols, memory, PostgreSQL/pgvector
│   │   ├── worker/           dispatch, input recovery, execution, persistence
│   │   ├── schemas/          pipeline state and domain/public models
│   │   ├── services/         file inspection, fingerprinting, quality scoring
│   │   ├── validators/       story validation
│   │   └── main.py           FastAPI application and demo routes
│   ├── migrations/           Alembic schema history
│   ├── scripts/              diagnostics, simulation, Studio helpers
│   ├── tests/                unit, API, worker, contract, and integration tests
│   ├── .env.example          safe configuration template
│   ├── Dockerfile            API/worker image
│   ├── langgraph.json        LangGraph Studio graph registration
│   └── pyproject.toml        Poetry dependencies and test settings
├── docs/                     canonical documentation
├── test-documents/           manual sample inputs
└── docker-compose.yml        PostgreSQL, Redis, migration, API, worker
```

## Major responsibilities

| Area | Responsibility | Primary locations |
|---|---|---|
| HTTP surface | FastAPI routes, CORS, request IDs, public/demo compatibility | `ai-service/app/main.py`, `ai-service/app/api/internal.py` |
| Job orchestration | Validation, idempotency, queue dispatch, status views | `ai-service/app/api/service.py`, `ai-service/app/services/fingerprint.py` |
| Pipeline | Graph construction and conditional routing | `ai-service/app/graph/pipeline.py`, `ai-service/app/graph/router.py` |
| AI stages | Ingest, transcription, extraction, classification, generation, quality | `ai-service/app/nodes/*.py` |
| Providers | Chat model fallback and embeddings | `ai-service/app/llm.py`, `ai-service/app/rag/embeddings.py` |
| Source grounding | Chunk indexing, BM25 retrieval, optional vector fusion, quote checks | `ai-service/app/rag/`, `ai-service/app/nodes/retrieve_evidence.py`, `evidence_grounding.py` |
| Durable state | Jobs, events, attempts, source artifacts, result decomposition | `ai-service/app/store/db/models.py`, `repositories.py` |
| Execution | In-process or Redis/RQ queue, worker recovery, streaming, persistence, callback | `ai-service/app/queue/`, `ai-service/app/worker/` |

## Important entry points

- `ai-service/app/main.py` → `app`, `POST /process`, `POST /process-json`, `GET /status/{job_id}`, `/health`, and `/ready`.
- `ai-service/app/api/internal.py` → protected `/internal/*` routes.
- `ai-service/app/graph/pipeline.py` → `build_pipeline()` and exported `graph`.
- `ai-service/app/worker/dispatch.py` → `dispatch_job()`.
- `ai-service/app/worker/main.py` → `run_job_entry()` for the RQ worker.
- `ai-service/app/worker/runner.py` → `execute_job()` and terminal-state handling.
- `ai-service/app/worker/state.py` → `make_initial_state()` and Redis/backend input reconstruction.
- `ai-service/app/store/factory.py` → selects memory or PostgreSQL stores from `DATABASE_URL`.
- `ai-service/app/queue/factory.py` → selects in-process or Redis/RQ queue from `REDIS_URL`.

## Communication model

The caller sends a job to the API. The API stores job metadata and dispatches work. The worker reconstructs transient input, invokes the same compiled graph used by tests and Studio, persists artifacts/results, and optionally calls the backend. A browser never calls the AI provider directly through this repository.

For the detailed trace, see [03-system-architecture.md](03-system-architecture.md) and [04-ai-pipeline.md](04-ai-pipeline.md).
