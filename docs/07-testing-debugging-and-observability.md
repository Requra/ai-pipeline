# Testing, Debugging, and Observability

Purpose: Show what is tested, how to run tests, and how to diagnose jobs cleanly. Audience: Developers, reviewers, and operators.

## Test commands

From `ai-service`:

```powershell
poetry run pytest -q
poetry run pytest tests/api tests/worker -q
poetry run pytest tests/nodes/test_prepare_sources_semantics.py tests/nodes/test_extract.py tests/nodes/test_retrieve_evidence.py -q
poetry run pytest tests/maintenance/test_retention_cleanup.py -q
poetry run pytest -m integration -q
```

The default suite is configured in `pyproject.toml` with `asyncio_mode = "auto"` and `pythonpath = ["."]`. `tests/conftest.py` forces empty `DATABASE_URL`/`REDIS_URL`, so the ordinary suite uses memory/in-process infrastructure. Integration tests are explicitly marked and require external configuration such as `TEST_DATABASE_URL`.

## Coverage map

| Test area | Representative locations | Verified behavior |
|---|---|---|
| API/auth/job lifecycle | `tests/api/test_internal_jobs.py`, `test_job_idempotency.py` | Auth, validation, queueing, status/result, idempotency, cancel, retry, request IDs. |
| Compatibility/security | `tests/api/test_internal_compatibility.py`, `tests/test_direct_contract.py` | Multipart multi-doc/audio routes, source recovery, SSRF checks, checksum behavior. |
| Graph/nodes | `tests/test_pipeline.py`, `tests/nodes/` | 13-node execution, source preparation, extraction, dedupe, retrieval, classification, grounding, generation, quality, repair, summary, formatting. |
| Source Processing | `tests/nodes/test_prepare_sources_semantics.py`, `tests/nodes/test_mixed_source_processing.py` | Bounded doc/audio preparation, PII redaction, audio STT fallback, partial failure. |
| Maintenance | `tests/maintenance/test_retention_cleanup.py` | Database retention cleanup for results, chunks, and embeddings. |
| Providers | `tests/test_llm_provider.py`, `test_llm_fallback.py`, `tests/nodes/test_transcribe.py` | Provider selection, fallback, timeout/error handling, STT mapping. |
| RAG | `tests/rag/`, `tests/nodes/test_semantic_conflict.py` | BM25, hybrid merge, embeddings, evidence retrieval, conflict candidates/fallback. |
| Contracts | `tests/test_contract_v1.py`, `test_direct_contract.py`, `tests/api/test_openapi_contract.py` | V1 completed/partial/failed result shapes and OpenAPI schema drift protection. |
| Worker | `tests/worker/test_runner.py` | Streaming updates, persistence failure, cancellation, crash mapping, callbacks. |
| Prompt assets | `tests/prompts/` | Registry completeness, UTF-8 loading/caching, snapshot hashes. |

## Debugging workflow

1. Confirm `/health`; then inspect `/ready` for safe dependency/configuration diagnostics.
2. Capture `X-Request-Id`, `job_id`, durable status, `current_node`, and `error_code`.
3. Read `GET /internal/jobs/{job_id}` and then `/result` after terminal status.
4. Inspect API and worker logs by job/request ID; events are persisted in `ai_job_events` on the PostgreSQL path.
5. Reproduce the smallest stage with a fixture in `tests/fixtures`, `test-fixtures/verification`, or `test-documents` and mock provider calls.
6. For a provider issue, run the focused LLM/STT tests and inspect provider selection without printing secrets or raw document content.

Useful diagnostic scripts include `poetry run python scripts/run_production_readiness_suite.py`, `scripts/simulate.py`, `scripts/check_models.py`, `scripts/llm_diagnostic.py`, and `scripts/evaluate_pipeline.py`.

## Grouped document fixture tests

The reusable grouped fixtures live in [`ai-service/test-fixtures/README.md`](../ai-service/test-fixtures/README.md). They cover complementary product documents and intentionally conflicting requirements. `tests/test_fixture_document_groups.py` runs the real LangGraph with deterministic provider responses. `scripts/run_fixture_uploads.py` uploads the same groups to a running service with the configured provider, polls the internal job endpoint, retrieves the result, and checks traceability and conflict signals.

## Logs, traces, and metrics

`app.main.request_tracing_middleware` logs method, path, status, duration, and request ID without bodies, query values, or headers. `app.progress` mirrors node progress for the legacy status path. The worker persists job start/finish/failure, node, attempt, and callback events. Provider calls attach provider/model/latency/token usage metadata where the client exposes it.

## Common symptoms

| Symptom | First checks |
|---|---|
| Job remains `QUEUED` | Worker process, Redis/RQ connectivity, queue name, migration/readiness, and input-cache write. |
| `SOURCE_INPUT_UNAVAILABLE` on retry | Redis cache expired and source document reference/content recovery is unavailable. |
| Partial result with warnings | Inspect `warnings`, `quality_issues`, `quality_report`, and the last node event; provider fallbacks may have kept the job alive. |
| Missing evidence | Check chunking, source index stats, retrieval mode, quote support, and project/tenant scope. |
| No audio output | File signature/size, ffmpeg, `ENABLE_AUDIO`, `TRANSCRIBE_PROVIDER`, and provider key. |
| Callback missing | Callback origin must match `BACKEND_BASE_URL`; inspect `callback_failed` event. Polling remains available. |
