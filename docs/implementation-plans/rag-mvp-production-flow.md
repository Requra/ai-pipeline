# Requra AI Pipeline — RAG MVP Production Flow Plan

> Living document. Updated at every internal checkpoint (one section per phase
> tracks status, changed files, tests run, results, known issues).

## 1. Branch

| Field | Value |
| --- | --- |
| Branch name | `harden/rag-mvp-production-flow` |
| Base branch | `harden/direct-demo-contract` (HEAD `2b302ce`) — the branch that matches the spec's "CURRENT KNOWN CONTEXT" (async polling, V1 contract, validation, CORS already landed). Not branched from `main` because `main` predates the working pipeline. |
| Date/time | 2026-06-29 |
| Owner/agent | Senior production AI engineer (Claude, Opus 4.8) |

## 2. Current State Summary

### API endpoints (public, must stay compatible)
- `GET /health` — liveness probe.
- `POST /process` — multipart upload (PDF/DOCX/TXT/audio). Returns `202 {job_id, status:"QUEUED"}`.
- `POST /process-json` — direct JSON text submission. Returns `202 {job_id, status:"QUEUED"}`.
- `GET /status/{job_id}` — async polling. Returns the stable status shape (`job_id, status, progress_pct, current_node, result, error, created_at, updated_at, completed_at`).

### Graph flow (as found)
```
detect_file_type → ingest → (route_after_ingest: transcribe | parse_to_chunks | format)
transcribe → parse_to_chunks
parse_to_chunks → extract → classify → evidence_grounding → generate
generate → quality_gate → summarize → format → END
```

### Contract
- `JobResult` (a.k.a. `PipelineResponseV1`, `contract_version="1.0"`) with V1 models:
  `SourceDocumentV1`, `RequirementV1`, `UserStoryV1`, `RequirementCoverageV1`,
  `ExportsV1` (excel + jira), `ArtifactsV1`, `StructuredSummary`, `QualityV1`,
  `PipelineError`, `PipelineWarning`, `QualityIssue`.
- Final public statuses: `completed | partial | failed | rejected`.

### Current strengths
- Clean node separation; LangGraph compiles; 100/101 tests green.
- Errors-as-data: nodes return `status`/`error`, the pipeline never hard-crashes the request.
- Evidence is already a first-class concept (`EvidenceSpan`, `evidence_grounding` node).
- Phase-1 API hardening largely already present: uuid4 job ids, 400/413/415 validation,
  env-driven CORS, status timestamps, `cleanup_expired_jobs` TTL.
- Prompt registry + loader + snapshot tests.

### Current risks / gaps
1. No true source retrieval/RAG index — evidence is validated, never *retrieved*.
2. `evidence_grounding` only validates; it cannot strengthen weak evidence.
3. No requirement deduplication (chunk overlap → duplicate requirements).
4. Generic story fallback (`"Requirement is implemented as specified"`).
5. In-memory progress store only (acceptable for MVP, but no abstraction seam).
6. Caller-provided `job_id` is **not** sanitized/validated.
7. No `/ready` readiness probe.
8. Raw model output is `print()`-logged in `extract` (leaks document text in prod logs).
9. Quality gate flags issues but produces no numeric groundedness/traceability score.
10. Story `type` in `format` is hard-coded to `"Functional"` for every story.
11. Tests prove execution, not MVP *quality* (no fixtures/thresholds/eval harness).

### Pre-existing test failure (documented, not caused by this branch)
- `tests/prompts/test_prompt_snapshots.py::test_prompt_snapshots` fails on this
  Windows checkout because `core.autocrlf=true` rewrites prompt files to CRLF in the
  working tree while the recorded SHA-256 hashes were taken on LF bytes. The snapshot
  hashes raw bytes, so CRLF≠LF. **Fix scheduled in Phase 3** (normalize line endings
  before hashing + add v2 hashes). Baseline therefore = **100 passed, 1 pre-existing
  CRLF failure**.

## 3. Target Final Flow

```
detect_file_type
→ ingest
→ transcribe              (only when file_type == audio)
→ parse_to_chunks
→ build_source_index      (NEW — Phase 2)
→ extract
→ dedupe_requirements     (NEW — Phase 4)
→ retrieve_evidence       (NEW — Phase 5)
→ classify
→ evidence_grounding
→ generate
→ quality_gate
→ summarize
→ format
```

Ordering note: `dedupe_requirements` runs **before** `retrieve_evidence` so retrieval
and classification operate on a clean, de-duplicated requirement set (cheaper, and
avoids classifying duplicates). `retrieve_evidence` runs **before** `classify` so the
classifier sees the strongest available evidence. This matches the spec's target order.

## 4. Architecture Principles

- **Backend remains the official caller.** The AI pipeline is an internal microservice;
  the .NET backend calls `/process` or `/process-json` and polls `/status`.
- **RAG is for source grounding, not chatbot Q&A.** Retrieval exists to attach/validate
  evidence quotes and improve traceability — never to answer free-form questions.
- **Contract-first.** No public `JobResult`/V1 field is removed or renamed. New internal
  state is additive and serializable.
- **Errors as data.** Nodes return `status`/`error`/`warnings`; a single bad chunk or LLM
  hiccup degrades gracefully (partial), it does not 500 the job.
- **Node-level responsibility.** Each node owns one concern and is independently testable.
- **Tests per node.** Every new node ships with deterministic, mock-LLM tests.
- **No raw document logging in production.** Raw text / full prompts / full LLM responses
  are never logged at INFO in prod; debug previews are gated behind a debug flag.
- **Backward compatibility.** Legacy fields (`functional_requirements`, `export_rows`,
  `error_message`, `source_fr_id`) are preserved.
- **No hosted infra requirement.** RAG is in-memory + deterministic lexical scoring; any
  heavier option stays optional and disabled by default.

## 5. Phase Plan

### Phase 1 — API/job hardening
- **Goal:** safe, predictable async API. Sanitize caller job ids; add a `JobStore`
  seam over the in-memory store; add `/ready`.
- **Files:** `app/services/job_store.py` (new), `app/main.py`, `app/progress.py`
  (kept back-compatible), `app/startup.py`, `tests/test_direct_contract.py`,
  `tests/test_ready.py` (new).
- **Acceptance:** existing API tests pass; caller-supplied ids validated; `/ready`
  reports safe diagnostics; no contract break.
- **Tests:** job-id sanitization (valid/invalid/too-long), `/ready` ok + shape,
  existing direct-contract + polling suites.
- **Commit:** `fix(api): harden async job lifecycle and status contract`
- **Rollback:** `git revert <sha>` (isolated to API layer).
- **Checkpoint status:** Passed
  - Changed: `app/services/__init__.py` (new), `app/services/job_store.py` (new),
    `app/startup.py` (+`build_readiness_report`), `app/main.py` (+`/ready`, job-id
    sanitization on both endpoints, `/status` via store), `tests/test_ready.py` (new).
  - Tests: `pytest -q` → **123 passed, 1 pre-existing CRLF snapshot failure**
    (23 new tests, 0 regressions).
  - Notes: `JobStore`/`MemoryJobStore` wrap the shared `progress_store` so legacy
    `update_progress` callers and the store stay in sync. `/ready` returns booleans +
    provider names only (no secrets); 503 when LLM provider unusable.

### Phase 2 — RAG source index foundation
- **Goal:** turn chunks into a retrievable in-memory lexical index.
- **Files:** `app/rag/__init__.py`, `app/rag/scoring.py`, `app/rag/lexical_retriever.py`,
  `app/rag/source_index.py`, `app/nodes/build_source_index.py`, `app/schemas/items.py`
  (add `RetrievedChunk`), `app/schemas/pipeline_state.py`, `app/graph/pipeline.py`,
  `tests/rag/*`, `tests/nodes/test_build_source_index.py`.
- **Acceptance:** deterministic top-k retrieval, no external DB, graph includes the node,
  empty input safe, existing tests pass.
- **Tests:** scoring determinism, retrieve relevant chunk, empty-chunks safety, graph compiles.
- **Commit:** `feat(rag): add in-memory source index and lexical retrieval`
- **Rollback:** `git revert <sha>`; node is additive (pass-through if disabled).
- **Checkpoint status:** Passed
  - Changed: `app/rag/{__init__,scoring,lexical_retriever,source_index}.py` (new),
    `app/nodes/build_source_index.py` (new), `app/schemas/items.py` (+`RetrievedChunk`),
    `app/schemas/pipeline_state.py` (+`source_index_id`, `retrieval_stats`),
    `app/graph/pipeline.py` (wire `parse_to_chunks → build_source_index → extract`),
    `tests/rag/test_lexical_retriever.py` (new), `tests/nodes/test_build_source_index.py` (new).
  - Tests: `pytest -q` → **137 passed, 1 pre-existing CRLF failure** (+14 new, 0 regressions).
  - Notes: deterministic BM25 (positive idf on tiny corpora); non-serializable retriever
    held in a bounded per-job registry (`app.rag.source_index`), state carries only the
    handle + primitive stats. Empty chunks → empty index + `SOURCE_INDEX_EMPTY` warning.

### Phase 3 — Grounded extraction upgrade
- **Goal:** stronger grounded extraction; JSON repair; stop raw logging.
- **Files:** `app/prompts/templates/extract_requirements_v2.md` (new),
  `app/prompts/registry.py`, `app/utils/json_parsing.py` (new), `app/nodes/extract.py`,
  `tests/prompts/test_prompt_snapshots.py` (CRLF-robust + v2 hash), `tests/nodes/test_extract*`.
- **Acceptance:** grounded extraction, no raw output logs in prod, malformed JSON does not
  crash the job, tests pass.
- **Commit:** `feat(extract): strengthen grounded extraction and JSON repair`
- **Rollback:** `git revert <sha>`; v1 prompt retained as fallback.
- **Checkpoint status:** Passed
  - Changed: `app/prompts/templates/extract_requirements_v2.md` (new),
    `app/prompts/registry.py` (+`EXTRACT_REQUIREMENTS_V2`), `app/utils/json_parsing.py` (new),
    `app/nodes/extract.py` (v2 prompt, JSON repair, debug-gated logging, confidence
    penalties, `extraction_type`, weak-evidence warning), `app/config.py` (+`DEBUG_LLM_IO`),
    `app/schemas/items.py` (+`extraction_type`), `tests/prompts/test_prompt_snapshots.py`
    (CRLF-robust + v2 hash + coverage test), `tests/utils/test_json_parsing.py` (new),
    `tests/nodes/test_extract_grounding.py` (new), `tests/test_e2e_mocked.py` (verbatim mock quote).
  - Tests: `pytest -q` → **154 passed, 0 failed** (+19 new). The pre-existing CRLF
    snapshot failure is now **resolved** (line-ending-normalized hashing).
  - Notes: malformed model JSON gets one LLM repair round before the chunk is dropped
    (never crashes the job); raw model output only logs at DEBUG and never in production;
    evidence confidence is graded exact(1.0)/fuzzy(x0.9)/fallback(x0.7); fallback evidence
    raises `EXTRACT_WEAK_EVIDENCE`, which correctly downgrades a run to `partial`.

### Phase 4 — Requirement deduplication node
- **Goal:** merge duplicate/near-duplicate requirements, preserve evidence, re-id.
- **Files:** `app/nodes/dedupe_requirements.py` (new), `app/graph/pipeline.py`,
  `tests/nodes/test_dedupe_requirements.py`.
- **Commit:** `feat(requirements): dedupe extracted requirements before classification`
- **Rollback:** `git revert <sha>`.
- **Checkpoint status:** Passed
  - Changed: `app/nodes/dedupe_requirements.py` (new), `app/graph/pipeline.py`
    (wire `extract → dedupe_requirements → classify`; **bind `recursion_limit=60`**),
    `tests/nodes/test_dedupe_requirements.py` (new).
  - Tests: `pytest -q` → **164 passed, 0 failed** (+10 new).
  - Notes: exact + near-dup (token Jaccard ≥ 0.8) merge; evidence unioned (never
    dropped); max confidence + strongest priority + label union preserved; different
    actor ⇒ kept separate + `POSSIBLE_DUPLICATE_REVIEW`; ids reassigned + legacy
    projection refreshed; warnings `DUPLICATE_REQUIREMENT_MERGED`/`POSSIBLE_DUPLICATE_REVIEW`.
  - **Important fix:** adding the 12th node tripped langgraph 0.0.26's default
    `recursion_limit=25` (this old BSP engine spends extra channel-propagation
    super-steps per node). Root-caused via step counting (`astream` completes at
    limit 100), fixed by binding `recursion_limit=60` on the compiled graph via
    `.with_config(...)` so all callers inherit it (no per-invoke config needed).
    The graph remains an acyclic DAG — this is only a step budget.

### Phase 5 — Evidence retrieval per requirement
- **Goal:** use the RAG index to strengthen per-requirement evidence + traceability.
- **Files:** `app/nodes/retrieve_evidence.py` (new), `app/graph/pipeline.py`,
  `tests/nodes/test_retrieve_evidence.py`.
- **Commit:** `feat(rag): retrieve supporting evidence for requirements`
- **Rollback:** `git revert <sha>`.
- **Checkpoint status:** Passed
  - Changed: `app/nodes/retrieve_evidence.py` (new), `app/graph/pipeline.py`
    (wire `dedupe_requirements → retrieve_evidence → classify`), `app/schemas/items.py`
    (+`evidence_match_score`, `quote_support_score`), `tests/nodes/test_retrieve_evidence.py` (new).
  - Tests: `pytest -q` → **171 passed, 0 failed** (+7 new).
  - Notes: per requirement, query = text+actor+goal → top-k lexical retrieval; best
    *sentence* snippet from NEW chunks attached (capped at 4 evidence/req, ≤240 chars —
    no full-chunk bloat); originals preserved; scores recorded; weak support
    (no grounded quote + no match) lowers confidence ×0.85 + flags review. Warnings:
    `WEAK_EVIDENCE_SUPPORT`, `NO_RETRIEVED_EVIDENCE`, `EVIDENCE_LIMIT_APPLIED`.

### Phase 6 — Generation quality improvements
- **Goal:** MVP-quality stories; validator; specific (non-generic) acceptance criteria.
- **Files:** `app/prompts/templates/generate_user_stories_v2.md` (new),
  `app/prompts/registry.py`, `app/validators/story_validator.py` (new),
  `app/nodes/generate.py`, snapshot test, `tests/nodes/test_generate*`,
  `tests/validators/test_story_validator.py`.
- **Commit:** `feat(generate): validate and repair generated stories`
- **Rollback:** `git revert <sha>`; v1 prompt retained.
- **Checkpoint status:** Passed
  - Changed: `app/prompts/templates/generate_user_stories_v2.md` (new),
    `app/prompts/registry.py` (+`GENERATE_USER_STORIES_V2`), `app/validators/story_validator.py`
    (new), `app/nodes/generate.py` (v2 prompt, `build_specific_acceptance_criteria`,
    fallback ACs, validation warning), snapshot test (+v2 hash),
    `tests/validators/test_story_validator.py` (new), `tests/nodes/test_generate_quality.py` (new),
    `tests/test_e2e_mocked.py` (v2-compliant generate mock).
  - Tests: `pytest -q` → **183 passed, 0 failed** (+12 new).
  - Notes: fallback stories now emit **≥2 requirement-specific GWT criteria** (the
    generic `"Requirement is implemented as specified"` is gone), type-aware
    (FR/NFR-measurable/BR-rule). A pure validator flags low-quality stories
    (insufficient/all-generic ACs, missing source ids/evidence ref, duplicates) →
    aggregate `GENERATE_STORY_QUALITY` warning; LLM stories are flagged, not mutated,
    so coverage stays consistent. v2 prompt enforces non-generic, type-specific ACs.

### Phase 7 — Quality gate scoring
- **Goal:** real numeric scores (traceability, groundedness, completeness, AC quality,
  duplicate risk, overall) mapped onto existing per-item `QualityV1`. No top-level
  contract change unless backward-compatible.
- **Files:** `app/nodes/quality_gate.py`, `app/services/quality_scoring.py` (new),
  `tests/nodes/test_quality_gate*`.
- **Commit:** `feat(quality): add groundedness and traceability scoring`
- **Rollback:** `git revert <sha>`.
- **Checkpoint status:** Not Started

### Phase 8 — Summary and export polish
- **Goal:** richer summary inputs; useful export rows; correct story-type mapping.
- **Files:** `app/nodes/format.py`, `app/nodes/summarize.py`, `tests/nodes/test_format*`,
  `tests/nodes/test_summarize*`.
- **Commit:** `feat(output): polish summary and export-ready rows`
- **Rollback:** `git revert <sha>`.
- **Checkpoint status:** Not Started

### Phase 9 — Test and evaluation harness
- **Goal:** make MVP quality measurable.
- **Files:** `tests/fixtures/*.txt` (5 fixtures), `scripts/evaluate_pipeline.py` (new),
  `tests/test_mvp_quality.py` (new).
- **Commit:** `test(pipeline): add MVP regression fixtures and evaluation harness`
- **Rollback:** `git revert <sha>` (tests/scripts only).
- **Checkpoint status:** Not Started

### Phase 10 — Documentation finalization
- **Goal:** team-readable docs.
- **Files:** `docs/rag-grounding-architecture.md` (new), `docs/node-reference.md` (new),
  `docs/contracts/pipeline-response-v1.md` (notes only), README/production-readiness,
  this plan's final status.
- **Commit:** `docs(pipeline): document RAG-grounded MVP production flow`
- **Rollback:** `git revert <sha>` (docs only).
- **Checkpoint status:** Not Started

## 6. Global Acceptance Criteria

The project is complete only when:
- [ ] API endpoints remain backward-compatible (`/health`, `/process`, `/process-json`, `/status`).
- [ ] Pipeline runs end to end (mocked LLM).
- [ ] Status endpoint returns the stable shape.
- [ ] Relevant input produces requirements and user stories.
- [ ] Irrelevant input is rejected gracefully (`status="rejected"`).
- [ ] Every requirement has evidence or an explicit warning.
- [ ] Every story links to requirement/source refs.
- [ ] Generated stories have non-generic acceptance criteria.
- [ ] Export rows exist when stories exist.
- [ ] Quality issues/warnings are meaningful and scores are derived (not faked).
- [ ] Tests pass (baseline + new).
- [ ] Docker build passes if Docker is available.
- [ ] Docs updated.

## 7. Backend Integration Notes
- **What backend calls:** `POST /process` (multipart file) or `POST /process-json`
  (`{job_id?, content, source_type?, source_documents?, metadata?}`). Both return
  `202 {job_id, status:"QUEUED"}`.
- **Polling:** `GET /status/{job_id}` until `status ∈ {COMPLETED, FAILED}`; on COMPLETED,
  `result` holds the `JobResult` (`contract_version="1.0"`).
- **Result payload:** consume `requirements`, `user_stories`, `requirement_coverages`,
  `summary`, `exports.excel.rows` / `exports.jira.rows`, `quality_issues`, `warnings`.
- **job_id:** backend MAY supply its own; it must match `^[A-Za-z0-9._-]{1,128}$` or the
  service returns `400` (Phase 1).
- **Known limitations:** in-memory job store (single process; not durable across restarts);
  XLSX binary is produced by backend from `exports.excel.rows` (AI service returns rows,
  not a file); RAG retrieval is lexical (no embeddings) by design for MVP.

## 8. Rollback Plan

```bash
# Abandon the whole branch (not merged):
git checkout harden/direct-demo-contract
git branch -D harden/rag-mvp-production-flow

# If pushed:
git push origin --delete harden/rag-mvp-production-flow

# Revert a single landed phase commit:
git revert <commit-sha>
```

Each phase is an isolated commit; new nodes are additive and degrade to pass-through,
so reverting any single phase leaves the pipeline runnable.
