# Pipeline fixture groups

Purpose: provide reusable document groups for testing the complete Requra.AI
pipeline from uploaded input through requirements, stories, evidence, quality,
and conflict handling. Audience: AI engineers, backend integrators, reviewers,
and developers validating a real provider deployment.

These fixtures complement the smaller unit fixtures under `tests/fixtures` and
the format/provider files under `test-fixtures/verification`.

## Fixture groups

| Group | Files | Intended behavior |
|---|---|---|
| `complementary/` | Three related product documents | Requirements from separate documents should be combined into one coherent result. |
| `conflicts/` | Two contradictory password-policy documents | Requirements should remain visible and produce a semantic conflict warning when conflict detection is enabled. |

The documents in a group are bundled with source-name separators by the live
runner because `POST /internal/process` accepts one multipart file per job. The
source names remain in the uploaded content so the result can be inspected.
True backend-owned multi-document retrieval is a separate path: submit
`source_documents` to `POST /internal/jobs` and provide either `file_url` or a
configured `BACKEND_BASE_URL` retrieval endpoint. See
`docs/11-endpoint-code-interactions.md`.

## What the complementary group checks

The three documents describe one project from different viewpoints:

1. Product scope and project workspace.
2. Authentication, roles, and audit behavior.
3. Requirements review and export behavior.

The result should demonstrate:

- Multiple requirements were extracted.
- User stories were generated.
- Every generated story maps to a source requirement.
- Requirements retain source evidence references.
- Stories have at least two acceptance criteria.
- The result is not rejected as irrelevant.
- The final result and exports are present.

## What the conflict group checks

The two documents intentionally disagree about password-reset authorization:

- One permits self-service email reset.
- One requires administrator approval.

Set `ENABLE_CONFLICT_DETECTION=true` before running the real test. A successful
conflict run should contain at least one warning whose code starts with
`SEMANTIC_` and at least one quality issue whose rule starts with
`semantic_conflict_`. The pipeline may still generate stories; the conflict is
reported as review information rather than automatically choosing a policy.

## Deterministic full-pipeline test

This test runs the real LangGraph and node sequence with deterministic provider
responses. It makes no network calls and costs nothing:

```powershell
cd C:\ITI_GP\src\ai-pipeline\ai-service
poetry run pytest tests/test_fixture_document_groups.py -q
```

This validates pipeline wiring and result invariants, but it does not validate
the quality of a real model response.

## Real provider upload test

Start the service with the desired provider configuration, PostgreSQL/Redis
when using production mode, and an internal service token. For conflict tests,
enable conflict detection and restart the service:

```powershell
$env:ENABLE_CONFLICT_DETECTION = "true"
$env:AI_INTERNAL_SERVICE_TOKEN = "<local-token>"

cd C:\ITI_GP\src\ai-pipeline\ai-service
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, upload and poll one group:

```powershell
cd C:\ITI_GP\src\ai-pipeline\ai-service
poetry run python scripts/run_fixture_uploads.py --group complementary
poetry run python scripts/run_fixture_uploads.py --group conflicts
```

The runner reads `AI_SERVICE_BASE_URL` (default `http://127.0.0.1:8000`) and
`AI_INTERNAL_SERVICE_TOKEN`. Override them explicitly when needed:

```powershell
poetry run python scripts/run_fixture_uploads.py `
  --base-url https://ai.example.com `
  --token $env:AI_INTERNAL_SERVICE_TOKEN `
  --group complementary
```

The runner:

1. Bundles the group files with filename separators.
2. Uploads the bundle through `POST /internal/process`.
3. Polls `GET /internal/jobs/{job_id}`.
4. Retrieves `GET /internal/jobs/{job_id}/result`.
5. Checks requirements, stories, traceability, acceptance criteria, and conflict signals.
6. Prints the job ID, status, current node, warnings, quality issues, and failures.

## Interpreting failures

| Failure | Meaning | First check |
|---|---|---|
| No requirements | Ingest rejected the text, extraction failed, or the provider returned unusable JSON. | `current_node`, `error`, `warnings`, and provider logs. |
| Stories without requirement IDs | Generation lost traceability or the provider response did not satisfy the contract. | `user_stories[*].requirement_id` and the generation logs. |
| Missing source references | Evidence grounding or format serialization failed. | `requirements[*].source_refs`, chunks, and retrieval logs. |
| Conflict group has no semantic warning | Conflict detection is disabled, candidate similarity was insufficient, or the provider did not classify the candidate pair. | `ENABLE_CONFLICT_DETECTION`, candidate text similarity, and `dedupe_requirements` logs. |
| Job fails during retry | The original source was not recoverable from Redis or the backend source reference. | `SOURCE_INPUT_UNAVAILABLE`, `file_url`, `document_id`, and `BACKEND_BASE_URL`. |

Real runs are evidence of the configured provider and deployment at that time;
they are not deterministic snapshots. Keep the job ID and result JSON with the
review record when investigating a provider regression.

