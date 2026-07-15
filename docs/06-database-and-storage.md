# Database and storage

Purpose: explain the durable entities, storage adapters, vector data, cache behavior, and migrations. Audience: backend, data, and operations engineers.

## Store selection

`app/store/factory.py` selects a `StoreBundle`:

- Empty `DATABASE_URL` → in-memory job, result, chunk, and embedding stores for tests/demo.
- Set `DATABASE_URL` → async PostgreSQL repositories with pgvector support.

`app/queue/factory.py` independently selects the in-process queue or Redis/RQ from `REDIS_URL`. This separation matters: Redis can disappear without being the source of truth, but a queued Redis job also needs transient input or durable backend references to be recoverable.

## Entity relationships

```mermaid
erDiagram
    AI_JOB ||--o{ AI_JOB_EVENT : records
    AI_JOB ||--o{ AI_JOB_ATTEMPT : has
    AI_JOB ||--o{ AI_SOURCE_DOCUMENT : owns
    AI_SOURCE_DOCUMENT ||--o{ AI_SOURCE_CHUNK : contains
    AI_SOURCE_CHUNK ||--o| AI_SOURCE_CHUNK_EMBEDDING : embeds
    AI_JOB ||--o{ AI_REQUIREMENT : produces
    AI_REQUIREMENT ||--o{ AI_REQUIREMENT_EVIDENCE : cites
    AI_JOB ||--o{ AI_USER_STORY : produces
    AI_USER_STORY ||--o{ AI_ACCEPTANCE_CRITERION : contains
    AI_JOB ||--o{ AI_REQUIREMENT_COVERAGE : maps
    AI_JOB ||--o| AI_QUALITY_REPORT : summarizes
    AI_JOB ||--o{ AI_QUALITY_ISSUE : flags
    AI_JOB ||--o{ AI_PIPELINE_WARNING : warns
    AI_JOB ||--o| AI_JOB_RESULT : stores
```

The ORM definitions are in `ai-service/app/store/db/models.py`; repositories map them to storage-agnostic Pydantic records from `app/store/models.py`.

## Main entities

| Table/domain record | Lifecycle and purpose |
|---|---|
| `ai_jobs` / `AiJobRecord` | One logical job id, request fingerprint, tenant/project, options, status, attempt, progress, errors, cancellation flag, and callback metadata. |
| `ai_job_attempts` | Attempt history and terminal error/status for retries. |
| `ai_job_events` | Node/job/callback observability events with safe metadata. |
| `ai_source_documents` | Backend references and source metadata; the AI service does not become long-term binary storage. |
| `ai_source_chunks` | Parsed text, source offsets/page/speaker/time, job and project scope. |
| `ai_source_chunk_embeddings` | Optional chunk vectors, provider model, and tenant/project scope for pgvector search. |
| `ai_requirements` / evidence | Decomposed extracted/classified requirements and source evidence. |
| `ai_user_stories` / criteria | Generated stories and acceptance criteria. |
| `ai_requirement_coverage` | Maps each requirement to stories or non-story/needs-review outcomes. |
| `ai_quality_report`, issues, warnings | Aggregate quality scores and detailed findings. |
| `ai_job_results` | Serialized `JobResult`, exports/artifacts JSON, contract version, status, and processing time. |

The baseline schema is created by `migrations/versions/0001_initial_schema.py`, which enables `vector`, creates ORM metadata, and adds an IVFFLAT cosine index. `0002_job_idempotency_fields.py` adds request fingerprint and duplicate-request indexes/fields. The migration chain is run with `alembic upgrade head`.

## Vector and retrieval storage

Chunk embeddings are generated only when a job enables embeddings and the provider is available. They are persisted to `ai_source_chunk_embeddings`; vector queries in `PgEmbeddingStore.search()` are filtered by job/project/tenant inputs. Hybrid retrieval combines vector candidates with the in-memory per-job BM25 index. Requirement embeddings used for semantic conflict candidate detection are intentionally transient model attributes and are not stored in the database or public response.

## Cache, deletion, and retention

`app/worker/state.py` stores inline text and demo file bytes in Redis under `aijob:input:{job_id}` for six hours. `app/worker/runner.py` clears the process-local source index after execution. Configured `JOB_RESULT_RETENTION_DAYS` and `CHUNK_RETENTION_DAYS` exist in settings, but this repository does not contain a scheduled cleanup job; enforcement is an operational responsibility/gap.

There is no implementation here for deleting backend-owned original files. Deleting AI-side rows must account for job, source, chunk, vector, result, event, and attempt relationships and should be implemented through the repository layer and migration policy rather than ad hoc SQL.

## Storage risks

- In-memory stores are non-durable and single-process.
- Redis input expiry can prevent retry recovery if the backend cannot supply source bytes/text.
- The source-index registry is process-local, so a worker rebuilds it for each job.
- PostgreSQL/pgvector behavior is covered by a marked integration test requiring `TEST_DATABASE_URL`; it is not validated by the default suite.
