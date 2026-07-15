# Local development

Purpose: provide the verified setup paths for tests, a single local API process, and the Docker Compose topology. Audience: new developers and contributors.

## Prerequisites

- Python 3.11.
- Poetry.
- Docker Desktop, only for the PostgreSQL/Redis topology.
- `ffmpeg` for local audio transcription outside the container; the Dockerfile installs it in the image.
- Provider credentials only for live LLM, embedding, or transcription calls.

## Install and configure

```powershell
cd ai-service
poetry install
Copy-Item .env.example .env
```

Edit `ai-service/.env` with placeholders or real local values. Never commit `.env`, provider keys, or `openai_key.txt`. The exact settings and defaults are listed in [09-security-and-configuration.md](09-security-and-configuration.md).

For a no-infrastructure test run, `ai-service/tests/conftest.py` explicitly sets `DATABASE_URL` and `REDIS_URL` to empty values, forcing memory stores and the in-process runner.

## Test-first local API

Run the unit/API suite:

```powershell
poetry run pytest -q
```

Start the API from `ai-service`:

```powershell
poetry run uvicorn app.main:app --reload --port 8000
```

Useful checks:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
```

`/health` is a dependency-free liveness response. `/ready` performs safe configuration/provider and optional infrastructure checks; it returns `503` when the service is not ready.

## Submit a text flow

The demo JSON route is unauthenticated and returns `202` with a queued job id:

```powershell
$body = @{ content = 'The system must allow users to reset passwords by email.'; metadata = @{} } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/process-json -ContentType 'application/json' -Body $body
```

Poll the returned id at `/status/{job_id}`. The internal route is the production-shaped path and requires `Authorization: Bearer <AI_INTERNAL_SERVICE_TOKEN>`.

## Docker Compose topology

Create `ai-service/.env`, then from the repository root:

```powershell
docker compose up --build
docker compose ps
Invoke-RestMethod http://localhost:8000/health
```

Compose starts PostgreSQL with pgvector, Redis, a one-shot Alembic migration container, the API, and the RQ worker. The API and worker share `DATABASE_URL` and `REDIS_URL`; Redis is dispatch/cache, not authoritative storage.

Run migrations manually only when operating the service outside Compose:

```powershell
cd ai-service
poetry run alembic upgrade head
```

## LangGraph Studio

The graph registration is in `ai-service/langgraph.json` (`app.graph.pipeline:graph`). The project also contains `poetry run studio` via `scripts.run_studio:main`; use it only after the Poetry environment and `.env` are configured.

## Common setup failures

| Symptom | Cause/check |
|---|---|
| `503` from `/ready` | Missing selected LLM key, enabled audio/embedding key, production token/origins, database, or Redis configuration; inspect the safe readiness body. |
| Tests attempt real infrastructure | Run from `ai-service`; `tests/conftest.py` must load and clear `DATABASE_URL`/`REDIS_URL`. |
| Audio fails before provider call | `ffmpeg` is missing or the file signature/size is invalid. |
| Docker API waits for migration | Check `docker compose logs migrate postgres`; migration must complete successfully. |
| Backend document job cannot start | The AI service must have an allowlisted `file_url` or a configured backend content endpoint and valid service token. |
| Internal route returns `401/403` | Send the bearer token matching `AI_INTERNAL_SERVICE_TOKEN`. |
