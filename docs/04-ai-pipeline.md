# AI pipeline

Purpose: trace the implemented Requra.AI AI flow from input to persisted output, including prompts, retrieval, validation, retries, and gaps. Audience: AI engineers, backend engineers, reviewers, and contributors changing pipeline behavior.

Code paths use the package shorthand `app/...` below; repository-relative, they are under `ai-service/app/...`.

## Implementation status legend

- **Implemented** means reachable from the current graph or worker/API code and covered by tests or direct source inspection.
- **Conditional** means implemented but controlled by configuration or per-job options.
- **Compatibility** means present for older callers, not the preferred production path.
- **Gap** means the repository does not implement the stronger behavior an older document claimed or a production system may eventually require.

## Entry points and inputs

### Production-shaped job entry point

`POST /internal/jobs` in `ai-service/app/api/internal.py` accepts `CreateJobRequest` from `ai-service/app/api/schemas.py`. Every `/internal/*` route depends on `require_internal_auth()` and therefore expects the bearer value configured in `AI_INTERNAL_SERVICE_TOKEN`.

The four input types are:

| Input type | Required input | Initial file type | Pipeline path |
|---|---|---|---|
| `text` | non-empty `content` | `text` | ingest → parse/chunk |
| `backend_transcript` | non-empty `content` | `transcript` | ingest → parse/chunk; no STT |
| `backend_document` | `source_documents` references | `document` | worker fetch/recovery → detect → ingest → parse/chunk |
| `backend_audio` | `source_documents` references | `audio` | worker fetch/recovery → detect → ingest → transcribe → parse/chunk |

`project_id` is required by the request schema; `tenant_id` is optional in the Python model but important for cross-tenant isolation. `content` and source references are validated in `create_job()` before job creation. The request fingerprint in `app/services/fingerprint.py` makes duplicate submission safe by distinguishing identical requests from reused job ids with different content/options.

Compatibility entry points are also real and tested:

- `POST /process-json` and `POST /process` in `ai-service/app/main.py` are unauthenticated demo/dev routes.
- `POST /internal/process-json` and `POST /internal/process` in `ai-service/app/api/internal.py` are protected compatibility routes.
- All routes funnel into the same dispatch and graph path; they are not separate pipelines.

## Pipeline overview

```mermaid
flowchart LR
    A["API job or compatibility upload"] --> B["Job validation, fingerprint, auth"]
    B --> C["Worker input recovery\nRedis cache or backend source"]
    C --> D["detect_file_type"]
    D --> E["ingest\nparse, normalize, PII mask, relevance"]
    E -->|audio| F["transcribe\nGroq or Deepgram"]
    E -->|document/text/transcript| G["parse_to_chunks"]
    F --> G
    E -->|rejected/error| N["format"]
    G --> H["build_source_index\nBM25 + optional chunk embeddings"]
    H --> I["extract\nstructured requirements"]
    I --> J["dedupe_requirements\nmerge + optional conflict detection"]
    J --> K["retrieve_evidence\nlexical or hybrid"]
    K --> L["classify"]
    L --> M["evidence_grounding"]
    M --> O["generate\nstories + acceptance criteria"]
    O --> P["quality_gate"]
    P -->|repair enabled and repairable issue| Q["repair_stories"]
    Q --> P
    P --> R["summarize"]
    R --> N
    N --> S["JobResult"]
    S --> T["persist result, status, events"]
    T --> U["polling or backend callback"]
```

The graph is built in `ai-service/app/graph/pipeline.py` by `build_pipeline()` and exported as `graph`. It registers 15 nodes. `PIPELINE_RECURSION_LIMIT = 60` is a LangGraph step budget, not a cycle count. The only graph loop is quality repair back to `quality_gate` and is bounded by `MAX_REPAIR_ATTEMPTS`.

## Detailed execution trace

| # | Stage | Input | Processing and output | Implementation |
|---:|---|---|---|---|
| 1 | Job/auth validation | HTTP request | Pydantic validation, input-type requirements, job-id sanitization, bearer auth for internal routes, fingerprint/idempotency decision. | `app/api/internal.py` → `create_job()`; `app/api/deps.py`; `app/api/service.py` → `handle_job_creation()` |
| 2 | Input reconstruction | Inline text/bytes, source refs, durable job | Redis input cache is preferred in Redis/RQ mode; missing cache can be rebuilt from PostgreSQL source references and backend text/content endpoints. | `app/worker/state.py` → `build_worker_initial_state()`; `app/clients/backend.py` |
| 3 | File detection | `raw_bytes` and declared type | Inspects signatures and size limits; emits `file_type` and `source_metadata` or an error. | `app/nodes/detect_file_type.py`; `app/services/file_inspection.py` |
| 4 | Ingest/relevance | Bytes, text, file type | Extracts PDF/DOCX/text, normalizes whitespace, masks detected emails/phones/secrets and Luhn-valid card candidates when enabled, then checks relevance with a snippet. LLM failure falls back to heuristic relevance. | `app/nodes/ingest.py` → `ingest_node()`, `_run_relevance_check()` |
| 5 | Audio transcription | Validated audio bytes | Validates ffmpeg, optionally compresses/splits audio into overlapping windows, calls configured Groq or Deepgram adapter, cleans transcript text and records source chunks. | `app/nodes/transcribe.py` → `transcribe_node()` |
| 6 | Chunking | Normalized text/transcript | PDF pages are preserved where available; other text uses a 3,000-character window with 500-character overlap. Chunks retain source/page/speaker/time offsets. | `app/nodes/parse_to_chunks.py` → `parse_to_chunks_node()` |
| 7 | Source index/embeddings | `chunks` | Builds a per-job in-memory BM25 `LexicalRetriever`. If `enable_embeddings` is true, generates chunk embeddings and persists them in the embedding store; failures are warnings, not immediate job failure. | `app/nodes/build_source_index.py`; `app/rag/source_index.py`; `app/rag/embeddings.py` |
| 8 | Requirement extraction | Chunks and source text | Sends chunk batches to a structured-output LLM prompt, normalizes labels and JSON variants, aligns evidence quotes to source text, and lowers confidence for weak/fallback evidence. | `app/nodes/extract.py` → `extract_node()`; `extract_requirements_v2.md` |
| 9 | Deduplication/conflicts | Extracted requirements | Exact/near duplicates are merged using normalized text/Jaccard similarity and actor rules. If conflict detection is enabled, optional in-memory requirement embeddings find candidates; batched LLM classification maps conflicts to warnings/issues. | `app/nodes/dedupe_requirements.py`; `app/rag/requirement_embeddings.py` |
| 10 | Evidence retrieval | Deduped requirements and source index | Retrieves up to three BM25 hits per requirement, optionally merges vector hits through `app/rag/hybrid.py`, caps evidence at four items, records scores, and lowers confidence for weak support. | `app/nodes/retrieve_evidence.py` → `retrieve_evidence_node()` |
| 11 | Classification | Requirements with evidence | Batches five requirements at a time, asks for FR/NFR/BR and special labels, clamps confidence, and applies a deterministic fallback when the LLM is unavailable. | `app/nodes/classify.py`; `classify_requirements_v1.md` |
| 12 | Grounding validation | Classified requirements, chunks | Verifies every evidence quote is non-empty and present in a source chunk; missing or non-verbatim evidence becomes a quality issue. | `app/nodes/evidence_grounding.py` |
| 13 | Story generation | Actionable classified requirements | Filters non-story labels, calls the v2 story prompt, maps returned requirement ids, normalizes actors/labels, creates acceptance criteria, preserves coverage, validates stories, and creates deterministic requirement-specific fallback stories when needed. | `app/nodes/generate.py`; `generate_user_stories_v2.md`; `app/validators/story_validator.py` |
| 14 | Quality/repair | Requirements, stories, coverage, issues | `quality_gate` checks evidence, confidence, story shape, acceptance criteria, duplicates, and coverage, then computes `quality_report`. If enabled and a repairable story issue remains, `repair_stories` calls its prompt and returns to the gate within the attempt limit. | `app/nodes/quality_gate.py`; `app/services/quality_scoring.py`; `app/nodes/repair_stories.py` |
| 15 | Summary/format | Final intermediate state | `summarize` creates a structured summary from a bounded artifact digest. `format` maps internal models to the public V1 `JobResult`, resolves `completed`/`partial`/`failed`/`rejected`, and removes internal embeddings/PII stats. | `app/nodes/summarize.py`; `app/nodes/format.py`; `app/schemas/items.py` |

### Routing rules

`route_after_ingest()` in `app/graph/router.py` returns `format` on state error or `is_useful == False`, `transcribe` for audio, and `parse_to_chunks` otherwise. `route_after_quality_gate()` returns `repair_stories` only when `ENABLE_QUALITY_REPAIR` is true, attempts remain, active story issues exist, and their rules are in `REPAIRABLE_RULES`; all other paths go to `summarize`.

## Prompt and model map

Prompt templates are executable source assets. `PromptId` and `PROMPT_MAP` in `app/prompts/registry.py` map ten ids to `app/prompts/templates/*.md`; `load_prompt()` reads UTF-8 content and caches it with `lru_cache`.

| Prompt | Caller | Composition/output |
|---|---|---|
| `ingest_relevance_v1` | `ingest_node` | System template plus bounded text snippet; structured relevance score/usefulness. |
| `extract_requirements_v2` | `extract_node` | System template plus one chunk; structured requirement list with evidence. |
| `detect_conflicts_v1` | `dedupe_requirements_node` | System template plus candidate requirement pairs; conflict classifications. |
| `classify_requirements_v1` | `classify_node` | System template plus batches of five requirements; labels/confidence. |
| `generate_user_stories_v2` | `generate_node` | System template plus formatted actionable requirements; stories and acceptance criteria. |
| `repair_stories_v1` | `repair_stories_node` | System template plus failed stories/issues; repaired story items. |
| `summarize_structured_v1` | `summarize_node` | System template plus bounded artifact digest; `StructuredSummary`. |
| `regenerate_story_v1` | `/internal/stories/regenerate` | System template plus requirement, original story, context, and feedback; one story response. |
| v1 extraction/generation templates | Registry compatibility | Registered and snapshot-tested assets; current nodes use the v2 extraction/generation templates. |

`app/llm.py` → `ResilientLLMClient` uses temperature `0`, provider-specific OpenAI-compatible clients, configured primary provider, and optional JSON `LLM_FALLBACK_CHAIN`. It enriches response metadata with provider/model/latency/token usage. `get_llm()` supports OpenRouter, OpenAI, and Groq. Structured nodes parse model text with `app/utils/json_parsing.py` and Pydantic models; malformed JSON may receive a repair attempt where the node uses `loads_with_llm_repair()`.

## Retrieval and context handling

This is source-grounding retrieval, not a standalone question-answering chatbot. The source index is stored in a bounded per-process registry keyed by job id; the `PipelineState` stores only its handle and lightweight stats. BM25 is always the primary local retriever. Hybrid mode adds PostgreSQL/pgvector vector hits and merges ranks; it does not replace quote verification. Chunk embeddings are persisted when enabled, while requirement embeddings used by conflict detection are held in memory and stripped from the public result.

## Persistence and result delivery

`execute_job()` in `app/worker/runner.py` adds an attempt, sets `PROCESSING`, streams node updates when supported, mirrors progress, and incrementally persists source documents/chunks after parsing/transcription. On a terminal graph result it calls `persist_result()`, writes the decomposed PostgreSQL rows through repositories, updates job status, records an event, clears the source index, and calls `_maybe_callback()` if configured. The result is available through `GET /internal/jobs/{job_id}/result` even when callback delivery fails.

## Failure matrix

| Failure | Detection | Retry/fallback | User-visible result | Logs/events |
|---|---|---|---|---|
| Missing/invalid internal token | `require_internal_auth()` | None | `401` or `403`; graph is not entered. | Auth warning. |
| Invalid input/metadata/job id | Pydantic or route checks | Caller fixes request | `400`, `413`, `415`, or `422`. | Request id/access log. |
| Unsafe, missing, too-large, or checksum-mismatched source | `BackendDocumentClient` and worker recovery exceptions | No provider retry; job fails or retry endpoint can create a new attempt after a terminal failure. | Failed job with `SOURCE_*` code. | Job event and sanitized warning/error. |
| Relevance rejects input | `ingest_node` and `route_after_ingest` | No downstream processing | `rejected`/completed public result with usefulness/relevance and warnings. | Node warning/event. |
| LLM transient provider error | `ResilientLLMClient` | Retryable provider errors and configured fallback providers; node-specific deterministic fallbacks may continue. | Partial/complete result depending on stage. | Provider/model/latency metadata and warnings; raw I/O only when enabled outside production. |
| STT provider failure | `transcribe_node` | Configured provider path/fallback behavior; otherwise pipeline failure. | Failed or partial result. | `TRANSCRIBE_*` error codes. |
| Empty/no index/no retrieval hits | Retrieval nodes | Continue with warnings and quote support checks. | Results may be partial or flagged for review. | `NO_RETRIEVED_EVIDENCE`/index warnings. |
| Bad structured output | Pydantic/JSON parsing and node validators | JSON extraction/repair and deterministic generation fallbacks where implemented. | Warning, partial result, or failed stage. | Node warning and safe error code. |
| Story quality issue | `quality_gate` | Optional bounded `repair_stories` loop. | Quality issues/report remain visible. | Quality events and warnings. |
| Job cancellation/timeout/crash | Worker checks cancel flag and runtime budget | Cancellation stops between nodes; `/retry` is allowed for failed/cancelled jobs only. | `CANCELLED` or `FAILED`. | Attempt and terminal events. |
| Result persistence failure | `persist_result()` exception | No silent success; runner marks failure. | `FAILED` and no false completion. | `PERSISTENCE_ERROR`. |
| Callback failure | `send_callback()` returns false or rejects origin | No durable outbox retry currently; polling remains available. | Persisted job unchanged. | `callback_failed` warning event. |

## Concept-to-code map

| Concept | Implementation | Entry point | Important dependencies |
|---|---|---|---|
| Unified graph | `app.graph.pipeline.build_pipeline()` | Worker and Studio | LangGraph `StateGraph`, `PipelineState` |
| Job lifecycle | `app.worker.runner.execute_job()` | `dispatch_job()` | `StoreBundle`, `JobStatus`, progress/events |
| Model fallback | `app.llm.ResilientLLMClient` | `get_llm()` | LangChain OpenAI-compatible clients, provider keys |
| Source index | `app.rag.source_index.build_source_index()` | `build_source_index_node()` | `LexicalRetriever`, BM25 scoring |
| Hybrid search | `app.rag.hybrid.merge_hits()` and DB vector search | `retrieve_evidence_node()` | pgvector, optional embedding provider |
| Public contract | `app.schemas.items.JobResult` and V1 models | `format_node()` | Pydantic |
| Quality repair | `repair_stories_node()` | Quality router | `REPAIRABLE_RULES`, repair prompt |
| Callback security | `BackendDocumentClient.send_callback()` | `_maybe_callback()` | backend origin allowlist, service token |

## Tests validating the flow

The main coverage is in `ai-service/tests/`:

- `tests/test_pipeline.py`, `tests/test_e2e_mocked.py`, and `tests/test_contract_v1.py` cover graph and public contract behavior.
- `tests/api/test_internal_jobs.py`, `test_job_idempotency.py`, and `test_internal_compatibility.py` cover auth, job lifecycle, compatibility routes, source security, cancellation, retry, and callbacks.
- `tests/nodes/` covers each major stage, grounding, quality, repair, audio, multi-document behavior, and fallbacks.
- `tests/rag/` covers BM25, hybrid merge, lexical retrieval, and vector-node behavior.
- `tests/prompts/` covers registry, loader, UTF-8, and snapshot protection.
- `tests/worker/test_runner.py` covers streaming fallback, persistence, cancellation, callback, crash, and status mapping.

## Known gaps and risky areas

- No frontend or backend implementation is available in this repository, so cross-service contract compatibility is verified only from the AI service's schemas/tests and the checked-in OpenAPI artifact.
- Callback delivery is best effort without a durable outbox or retry scheduler.
- Redis input cache expiry can make a job unrecoverable when durable backend source metadata/content is unavailable.
- Production requires a real PostgreSQL/pgvector and backend source-recovery contract; this repository cannot verify those external services here.
- Tenant/project fields are carried and used for durable vector filtering, but the Python request model permits a missing `tenant_id`; callers must supply it where isolation requires it.
- The source index registry is process-local and bounded; it is rebuilt by the worker rather than shared across workers.
