# 📋 Production Implementation Plan (Requra.AI Pipeline)

This plan outlines the roadmap to migrate the Requra.AI requirements extraction pipeline to a robust, production-ready system.

## 🚦 Phase Checkpoint Gate Rule
**No implementation phase may proceed without formal checkpoint approval**. Developers must pass all validation tests, satisfy the definition of done, and obtain team review sign-off before code merging or transitioning to the next phase.

---

## Phase 0 — Repository Audit and Rules Lock

### Goal
Assess current branch, lock coding standards in `rules.md`, audit codebase dependencies, and verify LangGraph skill integrations.

### Scope
- Workspace repository configuration audit.
- Rule enforcement in `rules.md`.
- No functional code modifications.

### Files Expected to Change Later
- [rules.md](../../rules.md) [NEW] (Completed in Phase 0)
- [docs/production-readiness/README.md](README.md) [NEW]

### Node Changes
None.

### New Schemas/Contracts
None.

### Validation Commands
```bash
# Verify rules file exists
cat rules.md
```

### Checkpoints
- Proposed `rules.md` is active in the repository root.
- Document audit is completed.
- Formal checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Audit fails or standard rules are rejected by the team.
- **Steps**: Disable development governance rules additions but maintain repository layout, keeping rules tracking files active.

### Risks
- Minor friction if the team does not align on rules during standup.

### Definition of Done
- `rules.md` locked and referenced.
- Technical documentation structure initialized.

---

## Phase 1 — Production Foundation and Dependency Safety

### Goal
Pin application dependencies, prepare containerized support for external processing packages, and implement startup environment verification.

### Scope
- Configure `pyproject.toml` with strict packages.
- Add `ffmpeg` and system library support in the Dockerfile.
- Implement startup checks for mandatory provider keys.

### Files Expected to Change Later
- [pyproject.toml](../../ai-service/pyproject.toml) [MODIFY]
- [Dockerfile](../../ai-service/Dockerfile) [MODIFY]
- [app/main.py](../../ai-service/app/main.py) [MODIFY]

### Node Changes
- **`main.py`**: Initialization logic calls a validation helper.

### New Schemas/Contracts
None.

### Validation Commands
```bash
poetry lock --no-update
poetry install
docker compose build ai-service
```

### Checkpoints
- [x] App fails to boot immediately if any active provider keys (based on `LLM_PROVIDER` and `TRANSCRIBE_PROVIDER`) are missing, showing a descriptive error. (Verified via startup.py)
- [x] Checkpoint approval obtained.

### Actual Results (Phase 1)
- Added dependencies: `pymupdf`, `python-docx`, `groq`, `httpx`, `pydub`.
- Docker: Added `ffmpeg`.
- Startup: Implemented `run_startup_checks` in `app/startup.py` supporting `LLM_PROVIDER` and `TRANSCRIBE_PROVIDER` validation.
- Validation: Verified strict failure in production mode (for invalid providers or missing keys) and warnings in development mode.

### Rollback Criteria & Steps
- **Trigger**: Docker build breaks or runtime initialization check blocks valid boot.
- **Steps**: Revert dependency version updates in `pyproject.toml` and reset `Dockerfile` to baseline packages, but do not bypass key validation or allow unauthenticated execution paths.

### Risks
- Potential compatibility issues during package locks.

### Definition of Done
- Locked dependencies.
- Docker builds successfully with `ffmpeg`.
- Keys verified at boot.

---

## Phase 2 — API Contract and State Schema Redesign

### Goal
Refactor internal graph state to leverage Pydantic models with explicit accumulation reducers.

### Scope
- Define Pydantic structures for documents, chunks, grounding, requirements, stories, and errors.
- Implement `PipelineState` with list reducers to prevent concurrent node overwrite.

### Files Expected to Change Later
- [app/schemas/pipeline_state.py](../../ai-service/app/schemas/pipeline_state.py) [MODIFY]
- [app/schemas/items.py](../../ai-service/app/schemas/items.py) [MODIFY]

### Node Changes
- All nodes updated to consume the new `PipelineState` typed schema.

### New Schemas/Contracts
- `DocumentSource`, `SourceChunk`, `EvidenceSpan`, `ExtractedRequirement`, `ClassifiedRequirement`, `RequirementCoverage`, `UserStory`, `QualityIssue`, `PipelineWarning`, `StructuredSummary`, `ExportRow`.

### Validation Commands
```bash
python -m compileall app
poetry run pytest tests/nodes/test_format.py
```

### Checkpoints
- Verification of Pydantic object instantiation.
- Reducers operate correctly in mock merges.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Pydantic validations fail to compile or break baseline compatibility.
- **Steps**: Disable advanced model validations but preserve grounding checks, keeping Pydantic type safety active for output structures. Do not return raw state dictionary objects.

### Risks
- Wide-reaching changes across nodes since state contracts are updated.

### Definition of Done
- No nodes use unstructured dictionaries.
- Pydantic models validate input/output interfaces correctly.

---

## Phase 3 — File Type Detection and Source-Aware Parsing

### Goal
Implement automated MIME-based file type detection and parse inputs into source-aware, metadata-preserved chunks.

### Scope
- Build `detect_file_type_node` replacing frontend type declarations.
- Build `parse_to_chunks_node` using `PyMuPDF` and `python-docx` to split text while retaining page numbers, paragraph positions, and formats.

### Files Expected to Change Later
- [app/nodes/detect_file_type.py](../../ai-service/app/nodes/detect_file_type.py) [NEW]
- [app/nodes/parse_to_chunks.py](../../ai-service/app/nodes/parse_to_chunks.py) [NEW]
- [app/nodes/ingest.py](../../ai-service/app/nodes/ingest.py) [MODIFY]
- [app/graph/pipeline.py](../../ai-service/app/graph/pipeline.py) [MODIFY]

### Node Changes
- **`detect_file_type`**: Receives bytes, parses magic bytes, sets `file_type`.
- **`ingest`**: Reads bytes and sanitizes text, stripping PII.
- **`parse_to_chunks`**: Receives sanitized text, outputs a list of structured `SourceChunk` items.

### New Schemas/Contracts
- `SourceChunk` list added to pipeline state.

### Validation Commands
```bash
poetry run pytest tests/nodes/test_ingest.py
```

### Checkpoints
- Text files chunked accurately.
- PDF pages mapped correctly to chunks.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Binary document stream parsing causes persistent exceptions.
- **Steps**: Disable automated parser mapping but reject unsupported extensions, keeping validation safety active. Do not trust client parameters blindly.

### Risks
- Parsing damaged or poorly formatted PDFs might cause PyMuPDF exceptions.

### Definition of Done
- Input files parsed into chunks.
- Every text chunk holds its source page/line reference.

---

## Phase 4 — Transcription Hardening

### Goal
Upgrade the transcription node to output structured chunk data containing speaker identities and timestamps, with resilient fallback policies.

### Scope
- Modify `transcribe_node` to return structured chunks.
- Setup retry policies and timeout configurations using the `langgraph-fundamentals` skill patterns.
- Validate `ffmpeg` presence at runtime.

### Files Expected to Change Later
- [app/nodes/transcribe.py](../../ai-service/app/nodes/transcribe.py) [MODIFY]

### Node Changes
- **`transcribe`**: Upgraded to return `List[SourceChunk]` with populated speaker ID and timestamps instead of a flat string.

### New Schemas/Contracts
- `SourceChunk` updates with `speaker` and `start_time_sec` fields.

### Validation Commands
```bash
poetry run pytest tests/nodes/test_transcribe.py
```

### Checkpoints
- Audio mock files process successfully.
- Speaker tags propagate correctly to downstream nodes.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Whisper API calls or speaker-alignment merges fail to parse.
- **Steps**: Disable speaker-turn mapping details but preserve evidence and strict contracts, mapping transcription outputs into a single default chunk with warning logs.

### Risks
- Large files may hit time-out limits under high loads.

### Definition of Done
- Transcription returns structured chunk data.
- Speaker references are maintained.

---

## Phase 5 — Requirement Extraction Redesign

### Goal
Refactor requirement extraction to support functional requirements, non-functional requirements (NFRs), constraints, business rules, assumptions, open questions, and out-of-scope items chunk-by-chunk.

### Scope
- Design chunk-by-chunk parallel LLM parsing.
- Extract multiple requirement types: FR, NFR, BR, Constraint, Assumption, Open Question, and Out-of-Scope.
- Enforce evidence quote capture (must be non-empty).

### Files Expected to Change Later
- [app/nodes/extract.py](../../ai-service/app/nodes/extract.py) [MODIFY]

### Node Changes
- **`extract`**: Processes individual chunks concurrently using the `Send` API or parallel orchestration, producing `extracted_requirements`.

### New Schemas/Contracts
- `EvidenceSpan`, list of `ExtractedRequirement` fields.

### Validation Commands
```bash
poetry run pytest tests/nodes/test_extract.py
```

### Checkpoints
- Extraction fails safely without generating hallucinated mock lists when LLM errors occur.
- Requirements have at least one backing evidence quote.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Concurrent execution overloading limits or failing validation.
- **Steps**: Disable parallel processing blocks but maintain multi-category extraction (FR, NFR, BR, Constraints, Assumptions, Open Questions, Out-of-Scope) and strict evidence checks.

### Risks
- Increased API cost due to concurrent chunk-by-chunk processing.

### Definition of Done
- All target requirement types extracted with source quote links.
- Parallel processing functions properly.

---

## Phase 6 — Classification and Deduplication

### Goal
Implement multi-label requirement classification and introduce a semantic deduplication step.

### Scope
- Map multi-label tags (e.g. FR + BR) with confidence scoring.
- Implement the deduplication node, merging items while combining and preserving their grounding citations, producing `classified_requirements`.

### Files Expected to Change Later
- [app/nodes/classify.py](../../ai-service/app/nodes/classify.py) [MODIFY]
- [app/nodes/deduplicate.py](../../ai-service/app/nodes/deduplicate.py) [NEW]
- [app/graph/pipeline.py](../../ai-service/app/graph/pipeline.py) [MODIFY]

### Node Changes
- **`classify`**: Evaluates confidence thresholds, mapping `extracted_requirements` to `classified_requirements`.
- **`deduplicate`**: Reconciles duplicate entities in the state.

### New Schemas/Contracts
- Multi-label classification arrays in `PipelineState`.

### Validation Commands
```bash
poetry run pytest tests/nodes/test_classify.py
```

### Checkpoints
- Merged requirements contain evidence pointers from both sources.
- Low-confidence classifications trigger review warnings.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Semantic matching collapses unrelated requirements.
- **Steps**: Disable semantic deduplication node in the graph but preserve evidence grounding and strict classification contracts.

### Risks
- Deduplication logic might accidentally merge unique but similar requirements.

### Definition of Done
- Duplicate requirements merged.
- Confidence thresholds enforced.

---

## Phase 7 — User Story Generation with Quality Control

### Goal
Refactor user story generation to support flexible requirement cardinality mappings (one-to-one, one-to-many, many-to-one, attached-as-criteria, non-story, needs_review), validating agile syntax and Given-When-Then criteria structure.

### Scope
- Generate user stories from requirements, maintaining category tags and evidence quotes.
- Run deterministic regex and structure validations on generated acceptance criteria.
- Support complex relationships (multiple requirements mapping to a single story, or requirements mapping to story criteria directly), writing `RequirementCoverage` logs.

### Files Expected to Change Later
- [app/nodes/generate.py](../../ai-service/app/nodes/generate.py) [MODIFY]

### Node Changes
- **`generate`**: Implements structured user story generation.

### New Schemas/Contracts
- `UserStory` object fields updated with `source_requirement_ids` and `RequirementCoverage` records.

### Validation Commands
```bash
poetry run pytest tests/nodes/test_generate.py
```

### Checkpoints
- Generated stories preserve requirement tag associations.
- Invalid Given-When-Then structures are caught programmatically.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Validation of card mappings fails or rejects too many stories.
- **Steps**: Revert to generating simple one-to-one story templates but do not generate hallucinated story details on failure.

### Risks
- LLM parser issues with complex acceptance criteria formats.

### Definition of Done
- Multi-card mapping logic enforced.
- Agile and Given-When-Then syntax validated.

---

## Phase 8 — Evidence Grounding, Quality Gate, and Repair

### Goal
Implement validation checks to ensure factual grounding, run quality analysis gates, and perform automated self-repair.

### Scope
- Build `evidence_grounding_node` to cross-examine output against source chunks, requiring a non-empty evidence list for all production requirements.
- Build `quality_gate_node` checking for duplicates, empty values, or missing actors.
- Build `repair_node` to handle self-correction prompts.

### Files Expected to Change Later
- [app/nodes/evidence_grounding.py](../../ai-service/app/nodes/evidence_grounding.py) [NEW]
- [app/nodes/quality_gate.py](../../ai-service/app/nodes/quality_gate.py) [NEW]
- [app/nodes/repair.py](../../ai-service/app/nodes/repair.py) [NEW]
- [app/graph/pipeline.py](../../ai-service/app/graph/pipeline.py) [MODIFY]

### Node Changes
- **`evidence_grounding`**: Flags requirements missing source quote support.
- **`quality_gate`**: Identifies structural faults.
- **`repair`**: Attempts self-correction, returning items marked `needs_review` if unfixable.

### New Schemas/Contracts
- `QualityIssue` lists.

### Validation Commands
```bash
poetry run pytest tests/nodes/test_quality_gate.py
```

### Checkpoints
- Grounding catches hallucinated requirements.
- Self-repair loop does not create infinite execution cycles.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Self-repair loop enters infinite cycles or degrades processing time.
- **Steps**: Disable the repair routing loop in the graph but route all quality violations to `needs_review` state. Do not bypass quality checking.

### Risks
- Infinite looping if the repair node fails to satisfy quality gate rules.

### Definition of Done
- Quality gate and grounding checks active.
- Self-repair loop functional.

---

## Phase 9 — Structured Summary and Export Formatter

### Goal
Structure executive summaries (StructuredSummary) and format output data for CSV, Excel, and Jira formats.

### Scope
- Rewrite `summarize_node` to cover key sections structurally, avoiding hallucinated default content on failure.
- Build `contract_formatter_node` to return the `JobResult` response structure.
- Build `export_formatter_node` for spreadsheet conversions.

### Files Expected to Change Later
- [app/nodes/summarize.py](../../ai-service/app/nodes/summarize.py) [MODIFY]
- [app/nodes/contract_formatter.py](../../ai-service/app/nodes/contract_formatter.py) [NEW]
- [app/nodes/export_formatter.py](../../ai-service/app/nodes/export_formatter.py) [NEW]
- [app/main.py](../../ai-service/app/main.py) [MODIFY]

### Node Changes
- **`summarize`**: Formats output structurally as `StructuredSummary`.
- **`export_formatter`**: Transforms objects to flat table entries, executing before contract formatting.
- **`contract_formatter`**: Prepares standard response output.

### New Schemas/Contracts
- `ExportRow`, `JobResult` schema returns.

### Validation Commands
```bash
poetry run pytest tests/nodes/test_summarize.py
```

### Checkpoints
- Response matches the `JobResult` schema.
- Export generates correct CSV structures.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Export formats fail to parse or cause validation crashes.
- **Steps**: Disable export rows list but keep strict `JobResult` contract validation active.

### Risks
- Minor compatibility issues with Excel formats on specific target clients.

### Definition of Done
- Structured summary fields mapped.
- Export structures conform to specifications.

---

## Phase 10 — Observability, Testing, and Production Readiness

### Goal
Configure logging frameworks, cost/token tracking metrics, and complete verification checks.

### Scope
- Implement structured logging across nodes.
- Track prompt and completion tokens.
- Add golden dataset evaluations.

### Files Expected to Change Later
- [app/llm.py](../../ai-service/app/llm.py) [MODIFY]
- [app/main.py](../../ai-service/app/main.py) [MODIFY]

### Node Changes
- All nodes record tracing information to the pipeline logs.

### New Schemas/Contracts
- Observability logs containing `job_id`, `trace_id`, and `token_usage` metrics.

### Validation Commands
```bash
poetry run pytest tests/test_pipeline.py
docker compose up ai-service
```

### Checkpoints
- Traces register correctly.
- Tokens tracked accurately.
- Checkpoint approval obtained.

### Rollback Criteria & Steps
- **Trigger**: Detailed metrics injection causes performance degradation.
- **Steps**: Reduce observability logs detail but keep structured JSON logging format active. Do not fallback to stdout print statements.

### Risks
- Performance overhead from metric logging.

### Definition of Done
- Full test pass.
- Structured logging active.
