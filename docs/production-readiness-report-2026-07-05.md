# AI Pipeline Production Readiness Report

Date: 2026-07-05  
Scope: pipeline execution, real-document processing, PostgreSQL/pgvector
persistence, Redis/RQ dispatch, API/worker lifecycle, and data consistency.

## Verdict

The pipeline is ready for a controlled production deployment with the tested
PostgreSQL + Redis topology. All automated tests, real-provider evaluations,
schema checks, restart durability checks, and post-rebuild smoke tests passed.

The local Docker deployment is currently running with `ENV=development`.
Production-mode validation was executed separately inside both rebuilt
containers and passed. Operators must set `ENV=production` in the actual
deployment and retain the validated secret/origin/database configuration.

## Verification results

- Full automated suite: **293 passed**, 0 failed.
- PostgreSQL integration tests: **4 passed** against the live pgvector database.
- Real OpenRouter evaluation: **5/5 documents passed** the MVP thresholds.
  - Four relevant documents completed with requirements, stories, full
    traceability, source references, at least two acceptance criteria per story,
    and exports.
  - The irrelevant document was correctly rejected.
- Live API/Redis/worker document runs:
  - Long CRM TXT: 68 requirements, 62 stories, 124 acceptance criteria, 68
    coverage rows, 204 evidence rows, one quality report, two warnings.
  - Small PDF and DOCX fixtures: correctly parsed and rejected as insufficiently
    relevant rather than failing.
- Post-rebuild real-model smoke run: completed with 2 requirements, 2 stories,
  and 4 acceptance criteria.
- Alembic:
  - Database revision is at `0002_job_idempotency` (head).
  - `alembic check` reports no schema drift.
- Runtime:
  - API, worker, PostgreSQL, and Redis containers are healthy/running.
  - `/ready` reports LLM, database, Redis queue, pgvector, internal auth, and
    CORS checks ready.
  - Production configuration and dependency validation pass in both application
    containers.
- Durability:
  - The 68-requirement result remained available and unchanged after rebuilding
    and restarting the API and worker.

## Data consistency findings

- JSON result counts match normalized PostgreSQL row counts.
- Exactly one result row, quality report, and attempt row exists per tested job
  and attempt.
- Completed attempts have terminal status and `completed_at`.
- No orphan results, chunks, attempts, requirement evidence, acceptance
  criteria, or embeddings were found.
- Retry persistence replaces prior chunks and embeddings instead of duplicating
  them.
- Vector retrieval now rejects unscoped searches, preventing accidental
  cross-job or cross-tenant reads.

## Fixes applied

- Prevented `.env` from re-enabling real Postgres/Redis during unit tests.
- Made final-result persistence failure terminal: a job is now marked
  `FAILED/PERSISTENCE_ERROR` instead of incorrectly reporting completion.
- Finalized failed and cancelled attempt records consistently.
- Aligned in-memory retry/upsert behavior with PostgreSQL behavior.
- Added graceful database engine disposal on API shutdown.
- Added Alembic handling for the intentional manual pgvector ANN index.
- Added regression tests for persistence failure, attempt finalization,
  retry-safe artifact replacement, and unscoped vector access.

## Remaining deployment caveats

- The checked local compose stack defaults to `ENV=development`; production must
  override this.
- Embeddings and hybrid retrieval are disabled in the current compose run.
  Lexical grounding is validated; enable and capacity-test semantic retrieval
  separately if it is required for launch.
- The suite emits 56 deprecation warnings from the older FastAPI/Starlette test
  client integration. They do not affect runtime correctness but should be
  removed during the dependency upgrade.
- Docker Compose warns that its top-level `version` key is obsolete.
- Load, soak, backup/restore, disaster recovery, alerting, and provider-failure
  drills are operational release checks and were not established by this
  repository-level validation.

