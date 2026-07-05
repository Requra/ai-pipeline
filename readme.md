# AI Pipeline — Architecture & Engineering Hub

This repository contains the Requra AI Pipeline, an internal microservice
(`ai-service`) built on **FastAPI + LangGraph**. It turns raw unstructured input
(briefs, transcripts, PDF/DOCX/TXT/audio) into structured requirements, classified
types, user stories with acceptance criteria, executive summaries, source
traceability, quality warnings, and Excel/Jira-ready rows.

It is **RAG for source grounding** (evidence/traceability/hallucination
reduction), not chatbot RAG. See [docs/rag-grounding-architecture.md](docs/rag-grounding-architecture.md).

## Structure
- `ai-service/`: the FastAPI + LangGraph microservice.
- `docs/`: contracts, node reference, RAG architecture, ADRs.

## Key docs
- [RAG grounding architecture](docs/rag-grounding-architecture.md)
- [Node reference](docs/node-reference.md)
- [Response contract v1](docs/contracts/pipeline-response-v1.md)
- [Implementation plan](docs/implementation-plans/rag-mvp-production-flow.md)

## Local setup

```bash
cd ai-service
poetry install
cp .env.example .env   # then fill in provider keys
poetry run uvicorn app.main:app --reload --port 8000
```

### Environment variables
| Var | Purpose | Default |
|-----|---------|---------|
| `ENV` | `development` / `production` (gates raw logging, CORS, fail-fast). | `development` |
| `LLM_PROVIDER` | `openrouter` \| `openai` \| `groq`. | `openrouter` |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GROQ_API_KEY` | provider key for the selected provider. | — |
| `TRANSCRIBE_PROVIDER` | `groq` \| `deepgram` (audio only). | `groq` |
| `ALLOWED_ORIGINS` | comma-separated CORS origins. Production requires explicit origins (no wildcard+credentials). | dev localhost list |
| `DEBUG_LLM_IO` | when `true` (and not production) logs raw LLM I/O at DEBUG. | `false` |

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness. |
| GET | `/ready` | Readiness (providers + DB/Redis/pgvector when configured). Safe diagnostics only; 503 when not ready. |
| POST | `/process` | Multipart file upload (PDF/DOCX/TXT/audio). Demo/dev. Returns `202 {job_id, status:"QUEUED"}`. |
| POST | `/process-json` | Direct text. Demo/dev. Returns `202 {job_id, status:"QUEUED"}`. |
| GET | `/status/{job_id}` | Poll job status/result (DB-backed in production). |
| POST | `/internal/jobs` | **Production**: create/enqueue a job (service token). See below. |
| GET | `/internal/jobs/{job_id}` | Durable job status. |
| GET | `/internal/jobs/{job_id}/result` | Persisted `JobResult` (409 if incomplete). |
| POST | `/internal/jobs/{job_id}/cancel` | Cancel a queued/running job. |
| POST | `/internal/jobs/{job_id}/retry` | Retry a terminal job (new attempt). |

> **Production mode** (durable Postgres+pgvector store, Redis/RQ worker,
> service-to-service auth, idempotency, cancellation, retry, backend callbacks,
> hybrid retrieval) is documented in
> **[docs/production-architecture.md](docs/production-architecture.md)**.
> `/internal/*` requires `Authorization: Bearer <AI_INTERNAL_SERVICE_TOKEN>`.
> The demo endpoints below stay backward-compatible and run the same
> DB-backed job + queue internally.

### Examples

```bash
# Direct text
curl -X POST localhost:8000/process-json -H 'content-type: application/json' \
  -d '{"content":"The system shall let users reset their password by email."}'
# -> {"job_id":"text_...","status":"QUEUED"}

# File upload (optional backend job id via metadata)
curl -X POST localhost:8000/process \
  -F 'file=@brief.pdf;type=application/pdf' \
  -F 'metadata={"job_id":"backend-123"}'

# Poll
curl localhost:8000/status/backend-123
```

`/status` shape (stable):
```json
{
  "job_id": "...", "status": "QUEUED|PROCESSING|COMPLETED|FAILED",
  "progress_pct": 0, "current_node": "...", "result": null, "error": null,
  "created_at": 0.0, "updated_at": 0.0, "completed_at": null
}
```
On `COMPLETED`, `result` holds the `JobResult` (contract v1).

## Tests & evaluation

```bash
cd ai-service
poetry run pytest                              # full unit/integration suite (mock LLM)
poetry run python scripts/evaluate_pipeline.py # MVP threshold harness (mock LLM, no keys)
poetry run python scripts/evaluate_pipeline.py --real  # uses configured provider
```

## Docker

```bash
# from repo root
docker compose build
docker compose up
```

## Backend integration (the .NET backend is the official caller)
1. `POST /process` (file) or `/process-json` (text); optionally supply a safe
   `job_id` (`^[A-Za-z0-9._-]{1,128}$`, else 400).
2. Poll `GET /status/{job_id}` until `status ∈ {COMPLETED, FAILED}`.
3. On `COMPLETED`, persist `result` (the `JobResult`). Consume `requirements`,
   `user_stories`, `requirement_coverages`, `summary`, `exports.excel.rows` /
   `exports.jira.rows`, `quality_report`, `quality_issues`, `warnings`.
4. Generate any binary `.xlsx` from `exports.excel.rows` on the backend — the AI
   service returns structured rows, not files.

**Local/Dev Limitations:** The default local/dev stack runs with an in-memory job store + per-job source index (single process; not durable across restarts) and uses BM25 lexical retrieval only.

**Production capabilities:** Setting `DATABASE_URL` enables durable PostgreSQL storage (along with `pgvector` for semantic and hybrid RAG retrieval), and setting `REDIS_URL` switches execution to a distributed RQ worker fleet.
