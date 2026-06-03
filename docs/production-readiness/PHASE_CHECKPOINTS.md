# 🚦 Phase Checkpoints & Gate Criteria

This document details the quality gates, pass/fail criteria, manual review checklists, and rollback safety procedures for each implementation phase.

## 🚦 Gate Rule
**No implementation phase may proceed without formal checkpoint approval**. Developers must pass all validation tests, satisfy the definition of done, and obtain team review sign-off before code merging or transitioning to the next phase.

---

## Phase 0 — Repository Audit and Rules Lock

* **Pass Criteria**: `rules.md` is present in the workspace root, containing instructions for using available LangGraph skills (`langgraph-fundamentals`, `langgraph-persistence`, `langgraph-human-in-the-loop`, `langchain-rag`).
* **Fail Criteria**: Missing or incomplete `rules.md` that does not reference skill locking or state safety principles.
* **Manual Review Checklist**:
  - [ ] Verify that all team members have read and approved the proposed rules.
  - [ ] Verify directory structure paths are correct.
* **Rollback Criteria & Steps**:
  - **Trigger**: Audit fails or standard rules are rejected by the team.
  - **Steps**: Disable development governance rules additions but maintain repository layout, keeping rules tracking files active.

---

## Phase 1 — Production Foundation and Dependency Safety

* **Pass Criteria**: Python dependencies are pinned and build in docker. Startup key checks prevent server boot if any API keys are missing.
* **Fail Criteria**: Code builds fail, or missing API keys trigger runtime exceptions during processing instead of boot failures.
* **Manual Review Checklist**:
  - [ ] Inspect the `Dockerfile` for `ffmpeg` and system utilities installation.
  - [ ] Run the server without `GOOGLE_API_KEY` and verify it halts startup.
* **Rollback Criteria & Steps**:
  - **Trigger**: Docker build breaks or runtime initialization check blocks valid boot.
  - **Steps**: Revert dependency version updates in `pyproject.toml` and reset `Dockerfile` to baseline packages, but do not bypass key validation or allow unauthenticated execution paths.

---

## Phase 2 — API Contract and State Schema Redesign

* **Pass Criteria**: `PipelineState` is defined as a TypedDict with explicit reducers (`operator.add`) for lists. API request/response models use Pydantic type definitions.
* **Fail Criteria**: State lists overwrite previous values due to missing reducers, or untyped dictionaries pass through interfaces.
* **Manual Review Checklist**:
  - [ ] Verify Pydantic validation handles malformed client payloads gracefully.
  - [ ] Trace list inputs through mock state updates to verify no entries are dropped.
* **Rollback Criteria & Steps**:
  - **Trigger**: Pydantic validations fail to compile or break baseline compatibility.
  - **Steps**: Disable advanced model validations but preserve grounding checks, keeping Pydantic type safety active for output structures. Do not return raw state dictionary objects.

---

## Phase 3 — File Type Detection and Source-Aware Parsing

* **Pass Criteria**: File detection node identifies PDF, DOCX, and text MIME signatures. Chunks contain correct page numbers and paragraph offsets.
* **Fail Criteria**: Client-side parameters determine file types, or chunking splits sentences in half, losing source references.
* **Manual Review Checklist**:
  - [ ] Test the pipeline with a multi-page PDF document.
  - [ ] Verify source pages map correctly to chunk metadata fields.
* **Rollback Criteria & Steps**:
  - **Trigger**: Binary document stream parsing causes persistent exceptions.
  - **Steps**: Disable automated parser mapping but reject unsupported extensions, keeping validation safety active. Do not trust client parameters blindly.

---

## Phase 4 — Transcription Hardening

* **Pass Criteria**: Transcribe node returns a list of source chunks with speaker IDs and time segments. Fallback from Groq to Deepgram (or vice-versa) triggers under network errors.
* **Fail Criteria**: Flat strings are returned, losing speaker metadata, or provider timeouts crash the service.
* **Manual Review Checklist**:
  - [ ] Test key validation and audio format processing.
  - [ ] Verify fallback executes properly when Groq API keys are temporarily revoked.
* **Rollback Criteria & Steps**:
  - **Trigger**: Whisper API calls or speaker-alignment merges fail to parse.
  - **Steps**: Disable speaker-turn mapping details but preserve evidence and strict contracts, mapping transcription outputs into a single default chunk with warning logs.

---

## Phase 5 — Requirement Extraction Redesign

* **Pass Criteria**: Nodes process source chunks in parallel. Requirements contain actor, goal, category label (supporting all expanded types including Open Questions and Out-of-Scope), and non-empty evidence quote metadata.
* **Fail Criteria**: Hallucinated fallbacks are returned under LLM error, or NFRs and Business Rules are missed.
* **Manual Review Checklist**:
  - [ ] Verify that every extracted requirement has a non-empty evidence quote.
  - [ ] Audit the parallel processing thread pool size under high load.
* **Rollback Criteria & Steps**:
  - **Trigger**: Concurrent execution overloading limits or failing validation.
  - **Steps**: Disable parallel processing blocks but maintain multi-category extraction (FR, NFR, BR, Constraints, Assumptions, Open Questions, Out-of-Scope) and strict evidence checks.

---

## Phase 6 — Classification and Deduplication

* **Pass Criteria**: Requirements support multi-label options. Duplicate requirements are merged, preserving and combining evidence quotes.
* **Fail Criteria**: Deduplication deletes unique requirements sharing identical actors, or classification results contain missing labels.
* **Manual Review Checklist**:
  - [ ] Verify duplicate entries combine their list of source references.
  - [ ] Check threshold constraints.
* **Rollback Criteria & Steps**:
  - **Trigger**: Semantic matching collapses unrelated requirements.
  - **Steps**: Disable semantic deduplication node in the graph but preserve evidence grounding and strict classification contracts.

---

## Phase 7 — User Story Generation with Quality Control

* **Pass Criteria**: Stories map to requirements using flexible cardinalities (one-to-one, one-to-many, many-to-one, etc.). Acceptance criteria match Agile syntax and Given-When-Then criteria rules.
* **Fail Criteria**: Stories missing required fields are passed through, or mapping mismatches occur.
* **Manual Review Checklist**:
  - [ ] Run regex validations on generated acceptance criteria.
  - [ ] Ensure requirement IDs match story sources correctly.
* **Rollback Criteria & Steps**:
  - **Trigger**: Validation of card mappings fails or rejects too many stories.
  - **Steps**: Revert to generating simple one-to-one story templates but do not generate hallucinated story details on failure.

---

## Phase 8 — Evidence Grounding, Quality Gate, and Repair

* **Pass Criteria**: Grounding node matches quotes back to text. The repair node loops back to fix errors and marks unfixable issues as `needs_review`.
* **Fail Criteria**: Hallucinated requirements bypass grounding checks, or infinite loops occur during repair.
* **Manual Review Checklist**:
  - [ ] Test repair logic by passing malformed requirement mock inputs.
  - [ ] Verify that the repair loop terminates after 2 retry cycles.
* **Rollback Criteria & Steps**:
  - **Trigger**: Self-repair loop enters infinite cycles or degrades processing time.
  - **Steps**: Disable the repair routing loop in the graph but route all quality violations to `needs_review` state. Do not bypass quality checking.

---

## Phase 9 — Structured Summary and Export Formatter

* **Pass Criteria**: Summaries contain structured sections. Export data matches Jira formatting rules.
* **Fail Criteria**: Summaries fail validation, or exports return malformed CSV data.
* **Manual Review Checklist**:
  - [ ] Test export capabilities using spreadsheet tools.
  - [ ] Confirm summary fields are populated.
* **Rollback Criteria & Steps**:
  - **Trigger**: Export formats fail to parse or cause validation crashes.
  - **Steps**: Disable export rows list but keep strict `JobResult` contract validation active.

---

## Phase 10 — Observability, Testing, and Production Readiness

* **Pass Criteria**: Metrics track duration, status, and tokens. Integration tests pass.
* **Fail Criteria**: Log outputs lack trace identifiers, or metrics cause performance degradation.
* **Manual Review Checklist**:
  - [ ] Audit the structured logs under load conditions.
  - [ ] Verify cost-tracking estimations match current vendor pricing models.
* **Rollback Criteria & Steps**:
  - **Trigger**: Detailed metrics injection causes performance degradation.
  - **Steps**: Reduce observability logs detail but keep structured JSON logging format active. Do not fallback to stdout print statements.
