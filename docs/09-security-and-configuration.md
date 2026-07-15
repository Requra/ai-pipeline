# Security and configuration

Purpose: state the implemented security boundaries and configuration contract, including missing protections. Audience: maintainers, security reviewers, and operators.

## Authentication and authorization

`app/api/deps.py` protects `/internal/*` with a bearer token. Missing configuration logs a development-only warning and leaves internal routes unauthenticated; production validation rejects that configuration. The service does not implement user sessions, project membership, or end-user authorization; the backend must enforce caller identity and project access before calling it.

Tenant/project values are carried into jobs, source rows, chunks, embeddings, and results. PostgreSQL retrieval filters are scoped by those values. Because `tenant_id` is optional in the request model, integration callers must supply it where tenant isolation is required.

## Source and network security

`BackendDocumentClient` enforces HTTP(S), rejects user-info URLs, validates approved/backend origins, blocks unsafe IPs/SSRF paths, does not follow arbitrary redirects, limits document/audio bytes, and verifies an optional SHA-256 checksum. The backend service token is attached only for the configured backend origin. Callback URLs are likewise rejected unless they match the configured backend origin.

## Secrets and sensitive data

Provider keys and service tokens are loaded from environment variables. Use `ai-service/.env.example` as the inventory; never commit `.env` or `openai_key.txt`. Logs redact request bodies, query values, headers, raw LLM I/O, and credentials by default. `DEBUG_LLM_IO` is force-disabled in production.

Ingest can mask detected emails, phones, API-key-like strings, and Luhn-valid credit-card candidates when `ENABLE_PII_MASKING` is enabled (default true). The masking statistics are internal state and are not exposed in the V1 result. This is pattern-based masking, not a complete data-loss-prevention system.

## AI-specific boundaries

- Uploaded documents and transcripts can contain sensitive meeting/project information and are sent to configured providers as prompt context. Provider data handling is outside this repository.
- Retrieved context is source-grounding context, not an authorization boundary by itself. Tenant/project scope and backend authorization must be correct before retrieval.
- Prompt injection defenses are not a separate implemented subsystem. Source text is inserted into extraction/relevance/generation contexts; reviewers should treat untrusted documents as instructions-capable content and validate outputs/evidence.
- Structured output parsing, quote checks, quality gates, and deterministic fallbacks reduce malformed or ungrounded output but do not guarantee factual correctness.

## Configuration inventory

| Variable | Purpose / default |
|---|---|
| `ENV` | Environment name; production enables fail-fast security/config checks. |
| `AI_INTERNAL_SERVICE_TOKEN` | Bearer token for `/internal/*`; required in production. |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins; explicit in production. |
| `DATABASE_URL` | PostgreSQL async DSN; empty selects memory stores. |
| `REDIS_URL`, `QUEUE_NAME` | Redis/RQ dispatch and transient input cache. |
| `LLM_PROVIDER`, `LLM_FALLBACK_CHAIN` | Primary/fallback chat routing. |
| `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY` | Chat provider credentials; `OPENROUTER_MODEL`, `OPENAI_MODEL`, `GROQ_MODEL` select models. |
| `TRANSCRIBE_PROVIDER`, `DEEPGRAM_API_KEY`, `GROQ_WHISPER_MODEL`, `ENABLE_AUDIO` | Audio provider and opt-in behavior. |
| `ENABLE_EMBEDDINGS`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `ENABLE_HYBRID_RETRIEVAL` | Chunk vector and hybrid retrieval controls. |
| `BACKEND_BASE_URL`, `BACKEND_SERVICE_TOKEN`, `CALLBACK_TIMEOUT_SECONDS` | Backend source recovery and callback trust boundary. |
| `ENABLE_PII_MASKING`, `DEBUG_LLM_IO` | Privacy/logging behavior; raw I/O is disabled in production. |
| `ENABLE_CONFLICT_DETECTION`, `CONFLICT_*` | Optional requirement conflict candidate/classification behavior. |
| `ENABLE_QUALITY_REPAIR`, `MAX_REPAIR_ATTEMPTS` | Optional bounded story repair loop. |
| `MAX_JOB_RUNTIME_SECONDS`, `MAX_CONCURRENT_JOBS`, `PROVIDER_TIMEOUT_SECONDS` | Worker/provider execution limits. |
| `JOB_RESULT_RETENTION_DAYS`, `CHUNK_RETENTION_DAYS` | Intended retention configuration; no cleanup scheduler is implemented here. |

## Gaps to track

- No application-level user/tenant authorization exists in this service.
- No durable callback outbox/retry exists.
- No complete prompt-injection policy or provider data-retention control exists in code.
- Pattern masking can miss sensitive data and can produce false positives.
- Rate limiting and abuse controls are not implemented in FastAPI routes in this repository.
