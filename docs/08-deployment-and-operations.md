# Deployment and Operations

Purpose: Document the deployment-shaped topology, operational behavior, CI gates, and maintenance routines. Audience: Platform engineers and release reviewers.

## Verified deployment shape

`docker-compose.yml` defines:

1. `postgres` using `pgvector/pgvector:pg16`.
2. `redis` using `redis:7-alpine`.
3. `migrate`, a one-shot API image running `alembic upgrade head`.
4. `ai-service`, the FastAPI process on port `8000`.
5. `ai-worker`, an RQ worker running `python -m app.worker.main`.

The API and worker share PostgreSQL and Redis. `Dockerfile` installs Python dependencies with Poetry and installs `ffmpeg`/`curl` in the image. The API healthcheck uses `/health`.

## Release and startup checks

The application lifespan calls `run_startup_checks()`; production configuration is validated by `collect_config_problems()` and `validate_required_config()`. In production, required LLM configuration, internal auth, explicit CORS origins, `DATABASE_URL`, and `REDIS_URL` (unless overridden) are fail-fast requirements. Optional audio and embeddings add requirements only when enabled.

Deploy in this order:

```powershell
docker compose build
docker compose up -d postgres redis
docker compose up migrate
docker compose up -d ai-service ai-worker
docker compose ps
```

## Continuous Integration & Release Gates

The repository includes GitHub Actions CI workflows in `.github/workflows/`:

- `.github/workflows/ci.yml`: Runs on push and pull requests; executes automated pytest test suite and contract drift checks with mock provider configuration.
- `.github/workflows/real_e2e_evaluation.yml`: Scheduled weekly / manual workflow; executes `scripts/run_production_readiness_suite.py` against real AI providers (Groq LLM + Groq Whisper + Neon PostgreSQL) and generates `docs/reports/MIXED_SOURCE_REAL_E2E_PROD_READINESS.md`.

## Migrations

Run `poetry run alembic upgrade head` in the migration container or from `ai-service`. The current chain is `0001_initial` → `0002_job_idempotency`. Do not use ORM auto-create as a production migration substitute; the migration explicitly enables pgvector and creates the IVFFLAT index.

## Maintenance and Retention Cleanup

To enforce retention policies for expired job results, chunks, and embeddings:

```bash
# Run standalone maintenance cleanup
python -m app.maintenance.cleanup
```

In production, schedule this command as a recurring cron job or container task.

## Health and rollback

- `/health` indicates that the API process is alive, not that dependencies are ready.
- `/ready` reports safe dependency/provider/configuration readiness and returns `503` when not ready.
- A rollback must preserve database migration compatibility with the image being rolled back to. Review `migrations/versions/` and job/result schema changes before reverting an image.
- Do not delete Redis keys during an incident until source recovery and queued-job consequences are understood.
- If the worker is unavailable, jobs may remain queued; inspect durable status before re-dispatching.

## Operational risks

- Callback delivery is one best-effort HTTP attempt; there is no durable outbox/retry worker.
- Redis is transient and input-cache expiry can make retries fail without backend raw-source recovery.
- Provider costs, rate limits, and token budgets are not centrally metered here; provider metadata is recorded opportunistically.
