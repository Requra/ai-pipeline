# Requra.AI

Requra.AI is a FastAPI and LangGraph service that turns requirement text, documents, transcripts, and meeting audio into structured requirements, source evidence, user stories, quality findings, summaries, and export rows. The repository contains the AI service; the calling backend and frontend are external systems.

## Architecture at a glance

`ai-service/app/main.py` exposes demo-compatible and service-to-service HTTP routes. Submissions become jobs, are dispatched through an in-process queue or Redis/RQ, executed by one 15-node LangGraph pipeline, persisted through in-memory or PostgreSQL/pgvector stores, and optionally sent to an allowlisted backend callback.

The AI service owns parsing, transcription, extraction, classification, grounding, story generation, quality checks, and result serialization. It does not own the backend's original file storage or frontend domain data.

## Repository map

- `ai-service/app/` — service code: API, graph, nodes, providers, retrieval, storage, and worker.
- `ai-service/tests/` — unit, API, worker, contract, prompt, and retrieval tests.
- `ai-service/migrations/` — Alembic migrations for PostgreSQL/pgvector.
- `ai-service/app/prompts/templates/` — versioned prompt assets loaded at runtime.
- `docs/` — canonical engineering documentation.
- `test-documents/` — local sample inputs for manual pipeline checks.
- `docker-compose.yml` — local production-shaped topology: PostgreSQL, Redis, API, worker, and migration job.

## Fastest local setup

The supported dependency workflow is Poetry with Python 3.11:

```powershell
cd ai-service
poetry install
Copy-Item .env.example .env
poetry run pytest -q
poetry run uvicorn app.main:app --reload --port 8000
```

The test suite forces in-memory stores and does not require PostgreSQL, Redis, or provider calls. API execution needs a configured LLM key; see [docs/02-local-development.md](docs/02-local-development.md).

For the full local topology, use `docker compose up --build` after creating `ai-service/.env` and setting provider keys.

## Start with the documentation

Read [docs/README.md](docs/README.md) for the reading order and canonical ownership rules. The most important technical reference is [docs/04-ai-pipeline.md](docs/04-ai-pipeline.md).

## Current maturity and limitations

The repository has a durable PostgreSQL/pgvector path and Redis/RQ worker path, but local tests default to in-memory execution. Embeddings, hybrid retrieval, conflict detection, audio, and quality repair are feature/configuration controlled. Callback delivery is best effort and has no durable outbox retry. Redis is transient; recovery of evicted inline or uploaded input depends on durable source references and the backend recovery contract.
