# Testing, debugging, and observability

Purpose: show what is tested, how to run it, and how to diagnose a job without leaking sensitive content. Audience: developers, reviewers, and operators.

## Test commands

From `ai-service`:

```powershell
poetry run pytest -q
poetry run pytest tests/api tests/worker -q
poetry run pytest tests/nodes/test_ingest.py tests/nodes/test_extract_grounding.py tests/nodes/test_retrieve_evidence.py -q
poetry run pytest -m integration -q
```

The default suite is configured in `pyproject.toml` with `asyncio_mode = "auto"` and `pythonpath = ["."]`. `tests/conftest.py` forces empty `DATABASE_URL`/`REDIS_URL`, so the ordinary suite uses memory/in-process infrastructure. Integration tests are explicitly marked and require external configuration such as `TEST_DATABASE_URL`.

## Coverage map

| Test area | Representative locations | Verified behavior |
|---|---|---|
| API/auth/job lifecycle | `tests/api/test_internal_jobs.py`, `test_job_idempotency.py` | Auth, validation, queueing, status/result, idempotency, cancel, retry, request ids. |
| Compatibility/security | `tests/api/test_internal_compatibility.py`, `tests/test_direct_contract.py` | Multipart/text routes, source recovery, SSRF/host checks, checksum/security behavior. |
| Graph/nodes | `tests/test_pipeline.py`, `tests/nodes/` | Routing, parsing, extraction, dedupe, retrieval, classification, grounding, generation, quality, repair, summary, formatting. |
| Providers | `tests/test_llm_provider.py`, `test_llm_fallback.py`, `tests/nodes/test_transcribe.py` | Provider selection, fallback, timeout/error handling, STT mapping. |
| RAG | `tests/rag/`, `tests/nodes/test_semantic_conflict.py` | BM25, hybrid merge, embeddings, evidence retrieval, conflict candidates/fallback. |
| Contracts | `tests/test_contract_v1.py`, `test_direct_contract.py` | Completed/partial/failed result shapes and compatibility status responses. |
| Worker | `tests/worker/test_runner.py` | Streaming updates, persistence failure, cancellation, crash mapping, callbacks. |
| Prompt assets | `tests/prompts/` | Registry completeness, UTF-8 loading/caching, snapshot hashes. |

The suite does not prove external backend, provider, live callback, deployment, or frontend behavior unless an explicitly configured integration test is run.

## Debugging workflow

1. Confirm `/health`; then inspect `/ready` for safe dependency/configuration diagnostics.
2. Capture `X-Request-Id`, `job_id`, durable status, `current_node`, and `error_code`.
3. Read `GET /internal/jobs/{job_id}` and then `/result` after terminal status.
4. Inspect API and worker logs by job/request id; events are persisted in `ai_job_events` on the PostgreSQL path.
5. Reproduce the smallest stage with a fixture in `tests/fixtures`, `test-fixtures/verification`, or `test-documents` and mock provider calls.
6. For a provider issue, run the focused LLM/STT tests and inspect provider selection without printing secrets or raw document content.

Useful diagnostic scripts include `poetry run python scripts/simulate.py`, `scripts/check_models.py`, `scripts/llm_diagnostic.py`, and `scripts/evaluate_pipeline.py`. Some scripts require real provider keys; inspect the script before running it.

## Grouped document fixture tests

The reusable grouped fixtures live in
[`ai-service/test-fixtures/README.md`](../ai-service/test-fixtures/README.md).
They cover complementary product documents and intentionally conflicting
requirements. `tests/test_fixture_document_groups.py` runs the real LangGraph
with deterministic provider responses. `scripts/run_fixture_uploads.py` uploads
the same groups to a running service with the configured provider, polls the
internal job endpoint, retrieves the result, and checks traceability and
conflict signals.

Use the deterministic test for CI and the live runner when validating provider
quality, configuration, and deployment behavior. Conflict detection must be
enabled for the conflict group; otherwise the absence of a semantic warning is
expected behavior.

## Logs, traces, and metrics

`app.main.request_tracing_middleware` logs method, path, status, duration, and request id without bodies, query values, or headers. `app.progress` mirrors node progress for the legacy status path. The worker persists job start/finish/failure, node, attempt, and callback events. Provider calls attach provider/model/latency/token usage metadata where the client exposes it.

There is no repository-wide metrics backend or distributed tracing exporter. Treat logs, durable events, status polling, and readiness as the currently implemented observability surface.

## Common symptoms

| Symptom | First checks |
|---|---|
| Job remains `QUEUED` | Worker process, Redis/RQ connectivity, queue name, migration/readiness, and input-cache write. |
| `SOURCE_INPUT_UNAVAILABLE` on retry | Redis cache expired and source document reference/content recovery is unavailable. |
| Partial result with warnings | Inspect `warnings`, `quality_issues`, `quality_report`, and the last node event; provider fallbacks may have kept the job alive. |
| Missing evidence | Check chunking, source index stats, retrieval mode, quote support, and project/tenant scope. |
| No audio output | File signature/size, ffmpeg, `ENABLE_AUDIO`, `TRANSCRIBE_PROVIDER`, and provider key. |
| Callback missing | Callback origin must match `BACKEND_BASE_URL`; inspect `callback_failed` event. Polling remains available. |
