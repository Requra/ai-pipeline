# Deployment and operations

Purpose: document the deployment-shaped topology and operational behavior that is present in the repository. Audience: platform engineers and release reviewers.

## Verified deployment shape

`docker-compose.yml` defines:

1. `postgres` using `pgvector/pgvector:pg16`.
2. `redis` using `redis:7-alpine`.
3. `migrate`, a one-shot API image running `alembic upgrade head`.
4. `ai-service`, the FastAPI process on port `8000`.
5. `ai-worker`, an RQ worker running `python -m app.worker.main`.

The API and worker share PostgreSQL and Redis. `Dockerfile` installs Python dependencies with Poetry and installs `ffmpeg`/`curl` in the image. The API healthcheck uses `/health`.

## Release and startup checks

The application lifespan calls `run_startup_checks()`; production configuration is validated by `collect_config_problems()`/`validate_required_config()`. In production, required LLM configuration, internal auth, explicit CORS origins, and `DATABASE_URL` are fail-fast requirements. Optional audio and embeddings add requirements only when enabled.

Deploy in this order:

```powershell
docker compose build
docker compose up -d postgres redis
docker compose up migrate
docker compose up -d ai-service ai-worker
docker compose ps
```

These commands are verified against the Compose service names and container commands; a live deployment was not performed as part of this documentation audit.

## Migrations

Run `poetry run alembic upgrade head` in the migration container or from `ai-service`. The current chain is `0001_initial` → `0002_job_idempotency`. Do not use ORM auto-create as a production migration substitute; the migration explicitly enables pgvector and creates the IVFFLAT index.

## Health and rollback

- `/health` indicates that the API process is alive, not that dependencies are ready.
- `/ready` reports safe dependency/provider/configuration readiness and returns `503` when not ready.
- A rollback must preserve database migration compatibility with the image being rolled back to. Review `migrations/versions/` and job/result schema changes before reverting an image.
- Do not delete Redis keys during an incident until source recovery and queued-job consequences are understood.
- If the worker is unavailable, jobs may remain queued; inspect durable status before re-dispatching.

## Operational risks

- Callback delivery is one best-effort HTTP attempt; there is no durable outbox/retry worker.
- Redis is transient and input-cache expiry can make retries fail without backend raw-source recovery.
- Retention settings exist but no scheduled cleanup implementation is present in this repository.
- Provider costs, rate limits, and token budgets are not centrally metered here; provider metadata is recorded opportunistically.
- There is no checked-in CI workflow in `.github/`; local tests and deployment checks must be wired into the external release system.
