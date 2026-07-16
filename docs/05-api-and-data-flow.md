# API and data flow

Purpose: document the important HTTP workflows without duplicating the generated OpenAPI file. Audience: backend/frontend integrators and API maintainers.

Code paths are repository-relative unless a command is explicitly described as running from `ai-service`.

The generated route artifact is [requra-ai-internal.openapi.json](../ai-service/docs/openapi/requra-ai-internal.openapi.json). The source of truth for request behavior is `ai-service/app/api/internal.py`, `app/api/schemas.py`, and `app/main.py`.

## Route groups

| Group | Routes | Auth | Role |
|---|---|---|---|
| Production-shaped jobs | `POST /internal/jobs`, `GET /internal/jobs/{job_id}`, `GET /internal/jobs/{job_id}/result`, `POST .../cancel`, `POST .../retry` | Bearer service token | Durable async job lifecycle. |
| Protected compatibility | `POST /internal/process`, `POST /internal/process-json` | Bearer service token | Existing multipart/text callers; funnels into the same job path. |
| Story feedback | `POST /internal/stories/regenerate` | Bearer service token | Stateless single-story regeneration using feedback. |
| Source recovery/diagnostics | `GET /internal/documents/{document_id}/content`, `POST /internal/jobs/{job_id}/callback-test` | Bearer service token; callback-test disabled in production | Backend source compatibility and guarded diagnostics. |
| Demo compatibility | `POST /process`, `POST /process-json`, `GET /status/{job_id}` | None | Local/demo submission and legacy polling. |
| Operational | `GET /health`, `GET /ready`, `/mock-doc-a`, `/mock-doc-b` | None | Probes and local mock sources. |

Every response carries or receives `X-Request-Id` through middleware in `app/main.py`; the server generates one when the caller does not provide it.

## Create and process a job

`CreateJobRequest` has `job_id`, `project_id`, optional `tenant_id`/caller identity, `input_type`, optional `content`, source references, options, and `reprocess`. `JobOptionsIn` carries story/summary flags, embeddings, hybrid retrieval, language, priority, and an optional callback URL. In the current graph, embeddings/hybrid/language/callback affect execution; `generate_user_stories` and `generate_summary` are persisted and fingerprinted but do not currently short-circuit the `generate` or `summarize` nodes.

```json
{
  "job_id": "run-123",
  "tenant_id": "tenant-1",
  "project_id": "project-1",
  "input_type": "backend_document",
  "source_documents": [{
    "document_id": "doc-1",
    "file_type": "pdf",
    "mime_type": "application/pdf",
    "file_url": "https://backend.example/source/doc-1",
    "sha256_hash": "<sha256>"
  }],
  "options": {"generate_user_stories": true, "generate_summary": true}
}
```

The route returns `202` when a new attempt is queued. A same-fingerprint duplicate may return an idempotent status/result response; a different request reusing an active job id is a conflict. `reprocess: true` can create a new attempt only under the rules in `handle_job_creation()`; the retry route explicitly permits failed/cancelled jobs.

## Status and result workflow

1. Caller submits `POST /internal/jobs`.
2. Caller polls `GET /internal/jobs/{job_id}` or uses the links in the response.
3. Caller reads `GET /internal/jobs/{job_id}/result` after terminal completion.
4. Before a result exists, the result route returns `409` with the current status.
5. If `options.callback_url` is set and the URL matches the configured backend origin, the worker posts a terminal payload. Polling remains the reliable retrieval mechanism when callback delivery fails.

Durable job statuses are `QUEUED`, `PROCESSING`, `COMPLETED`, `FAILED`, `CANCELLED`, `PARTIAL`, and `REJECTED`. The public legacy status view maps `PARTIAL` and `REJECTED` to `COMPLETED`; the detailed result carries its lowercase contract status.

## Main data flows

| Workflow | Entry | Service calls | Persistence/output |
|---|---|---|---|
| Text | `/internal/jobs` with `input_type=text` and `content` | Create/fingerprint → dispatch → ingest → full graph | Job, chunks, result, events; poll/callback. |
| Transcript | `input_type=backend_transcript` or compatibility JSON `source_type=meeting_transcript` | Inline content → ingest → full graph without STT | Same result contract; transcript source type is retained in source metadata where available. |
| Document | `backend_document` source reference or multipart compatibility route | Worker source recovery → MIME detection → PDF/DOCX extraction → graph | Source document/chunk records and V1 result source refs. |
| Audio | `backend_audio` reference or multipart compatibility upload | Source recovery → signature/size guard → STT → transcript chunks → graph | Audio source metadata, transcript-grounded result, job status. |
| Story regeneration | `/internal/stories/regenerate` with requirement, original story/context, and feedback | Auth → prompt composition → one LLM call → JSON parse/validation | Stateless `RegenerateStoryResponse`; no job/result persistence. |
| Cancellation | `/internal/jobs/{id}/cancel` | Set cooperative cancel flag | Worker observes between nodes and ends `CANCELLED`. |
| Retry | `/internal/jobs/{id}/retry` | Check terminal retryable status → increment attempt → dispatch | Same job id with new attempt; source recovery may still fail if external source is unavailable. |

## Error behavior

Route validation uses `400` for caller-specific semantic errors, `401/403` for internal auth, `404` for unknown jobs, `409` for unavailable results/conflicts/non-retryable lifecycle actions, `413` for file size, `415` for media type, and `422` for Pydantic validation. Worker failures are represented in durable status/error fields and the `PipelineError` in `JobResult` rather than returned as a synchronous HTTP error after `202`.

## Contract ownership

Request models live in `ai-service/app/api/schemas.py`; internal domain records live in `app/store/models.py`; public V1 result models live in `app/schemas/items.py`; serialization occurs in `app/nodes/format.py`. Do not use an old contract report as authority when it disagrees with these files.
