# Contributor Onboarding

Purpose: Give a new developer a practical first path and safe change rules. Audience: Contributors working on the API, worker, retrieval, or AI stages.

## First day

1. Read [01-codebase-overview.md](01-codebase-overview.md), [03-system-architecture.md](03-system-architecture.md), and [04-ai-pipeline.md](04-ai-pipeline.md).
2. From `ai-service`, run `poetry install`, copy `.env.example` to `.env`, and run `poetry run pytest -q`.
3. Start the API and check `/health` and `/ready`.
4. Submit a small `/process-json` text request and poll `/status/{job_id}`.
5. Open `app/graph/pipeline.py`, `app/worker/runner.py`, `app/schemas/pipeline_state.py`, and the tests for the stage you plan to change.

## How to trace a feature

Start at the route in `app/main.py` or `app/api/internal.py`, follow `app/api/service.py` into `dispatch_job()`, then `build_worker_initial_state()` and `execute_job()`. From there follow the graph edge in `app/graph/pipeline.py`, the node's returned state keys, and the corresponding tests. For output changes, finish at `format_node()` and `JobResult` in `app/schemas/items.py`.

## Common safe changes

| Change | Inspect first | Update/test |
|---|---|---|
| Add or change an endpoint | `app/api/internal.py`, `app/api/schemas.py`, auth dependency | OpenAPI artifact policy (`docs/openapi.json`), API tests, status/error docs. |
| Modify a pipeline stage | `app/graph/pipeline.py`, `PipelineState`, neighboring node contracts | Focused node tests, pipeline/contract tests, [04-ai-pipeline.md](04-ai-pipeline.md). |
| Modify source prep / STT | `app/nodes/prepare_sources.py`, `app/services/source_processing/` | Source prep tests, STT fallback tests, [04-ai-pipeline.md](04-ai-pipeline.md). |
| Change a prompt | `app/prompts/registry.py`, template, prompt snapshot hashes | Prompt loader/registry/snapshot tests and provider-mocked stage tests. |
| Change a public result field | V1 models and `format_node.py` | Contract tests, backend integration review, API/data-flow docs. |
| Change storage | `app/store/base.py`, memory store, ORM models, repositories | Migration, memory tests, marked DB integration test, storage docs. |
| Change provider fallback | `app/llm.py`, config/startup, tests | Provider/fallback/timeout tests and failure matrix. |
| Change source handling | `app/clients/backend.py`, file inspection, worker recovery | SSRF/checksum/size/source-recovery tests and security docs. |

## AI change checklist

- State the input and output fields for the node.
- Keep source evidence and quote validation intact when changing extraction/retrieval/generation.
- Keep tenant/project filters on persistent retrieval.
- Use the prompt registry; do not inline a second copy of a template.
- Preserve structured parsing and deterministic fallback behavior where applicable.
- Add provider-mocked tests before using a live provider.
- Update the canonical AI pipeline document only after confirming behavior in source/tests.

## Pull request expectations

Run `poetry run pytest -q` and any focused tests. Include migrations for schema changes, avoid secrets and raw prompt/document logs, describe feature-flag/default changes, and call out any external backend/provider contract dependency. Documentation changes belong in the one canonical document listed by [docs/README.md](README.md).

## Avoid these mistakes

- Do not call the AI service's 13-node graph “14 nodes” or “15 nodes.”
- Do not treat Redis as durable storage or assume a retry can recover expired input without backend source references.
- Do not assume a callback means the result is persisted at the receiver; polling is the durable retrieval path.
- Do not add frontend/backend behavior to this repository's documentation as if it were implemented here.
- Do not keep completed plans, generated reports, or duplicated endpoint walkthroughs as permanent Markdown.
