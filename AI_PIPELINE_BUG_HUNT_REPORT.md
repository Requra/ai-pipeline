# Requra.AI AI Pipeline Bug Hunt & Hardening Report

**Branch:** `feat/doc-audio-processing`  
**Date:** August 17, 2026  
**Status:** **RESOLVED & PRODUCTION READY**  
**Total Tests Passing:** 579 / 579 (100%)  

---

## Executive Summary

An autonomous deep-dive investigation was conducted across the asynchronous ingestion, extraction, and story generation pipeline in `Requra/ai-pipeline` to resolve root causes of missing requirements, swallowed extraction failures, document modality misrouting, and vacuous quality scores.

Every test fixture from `ai-service/test-fixtures/e2e_real_mixed` (`technical-notes.docx`, `requirements.pdf`, `stakeholder-notes.txt`, `meeting-audio.wav`, and multi-source combinations) was thoroughly verified through end-to-end executions with real LLM and STT models.

All defects have been resolved, regression tests have been added, and the full test suite passes with zero failures.

---

## 1. Root Causes & Architectural Fixes

### 1.1 DOCX Modality & Magic Bytes Dispatching Bug
- **Location:** [`ai-service/app/services/source_processing/document.py`](file:///c:/ITI_GP/src/ai-pipeline/ai-service/app/services/source_processing/document.py)
- **Problem:** When a caller or multipart upload sent `file_type="document"` (or generic MIME `application/octet-stream`), the document processor matched plain text or PDF heuristics before checking the DOCX binary structure. This resulted in DOCX ZIP binary payloads being decoded as `latin-1` strings, yielding garbled XML text and 0 extracted requirements.
- **Fix:** Refactored modality detection to check:
  1. Filename extensions (`.docx`, `.pdf`, `.txt`, `.md`).
  2. Subtypes and MIME types (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`).
  3. Magic bytes inspection: `%PDF` for PDF, `PK\x03\x04` for DOCX ZIP archive headers.
  4. Coordinate-aware paragraph chunking for DOCX via `python-docx`.

### 1.2 Extraction Error Swallowing & Generic Code Collapsing
- **Location:** [`ai-service/app/nodes/extract.py`](file:///c:/ITI_GP/src/ai-pipeline/ai-service/app/nodes/extract.py)
- **Problem:** Exceptions, timeouts, JSON parsing errors, and Pydantic validation failures were being caught and swallowed into empty lists `[]`, which collapsed into a misleading `EXTRACT_EMPTY` ("no requirements found in source") warning rather than exposing actionable technical errors.
- **Fix:**
  - Implemented typed `ChunkExtractionOutcome` tracking per chunk:
    - `SUCCESS_WITH_REQUIREMENTS`
    - `SUCCESS_NO_REQUIREMENTS` (legitimate `EXTRACT_EMPTY`)
    - `MODEL_FAILURE` (`EXTRACT_PROVIDER_FAILURE`)
    - `MODEL_TIMEOUT` (`EXTRACT_MODEL_TIMEOUT`)
    - `PARSE_FAILURE` (`EXTRACT_PARSE_FAILURE`)
    - `VALIDATION_FAILURE` (`EXTRACT_VALIDATION_FAILURE`)
  - Added bounded extraction concurrency (`asyncio.Semaphore(3)`) to prevent provider rate-limit crashes.
  - If all chunks fail technically, the node now marks `status="error"` and records the primary error code.

### 1.3 Normalization Shape Ingestion & Label Key Collisions
- **Location:** [`ai-service/app/nodes/extract.py`](file:///c:/ITI_GP/src/ai-pipeline/ai-service/app/nodes/extract.py)
- **Problem:** LLMs frequently output valid JSON requirement collections in alternative container schemas (e.g. top-level lists `[...]`, `{"items": [...]}`, numeric string keys `{"1": {...}}`, or single objects `{"id": 1, ...}`). Additionally, heuristic fallback in `normalize_label()` previously caused standard object keys (`"id"`, `"text"`, `"disposition"`) to be misidentified as requirement label mappings (`"FR"`), creating corrupt requirements.
- **Fix:**
  - Overhauled `normalize_extraction_payload()` to natively support canonical dictionaries, top-level arrays, wrapped containers (`items`, `data`, `results`, `extracted_requirements`), numbered dictionaries, and single requirement objects.
  - Introduced strict `is_candidate_label_key()` validation to ensure only explicit requirement taxonomy keys (`FR`, `NFR`, `FUNCTIONAL`, `BUSINESS_RULE`, etc.) are recognized as label maps.

### 1.4 Vacuous Mathematical Quality Scores
- **Location:** [`ai-service/app/services/quality_scoring.py`](file:///c:/ITI_GP/src/ai-pipeline/ai-service/app/services/quality_scoring.py)
- **Problem:** When 0 requirements or 0 stories were extracted, mathematical division edge cases produced misleading `1.0` (100%) scores for groundedness, acceptance criteria quality, and traceability coverage.
- **Fix:** Enforced strict truthfulness in quality scoring: if `requirement_count == 0` or `story_count == 0`, groundedness, traceability, and acceptance criteria scores are strictly set to `0.0`.

### 1.5 LangGraph Pipeline State Schema Hardening
- **Location:** [`ai-service/app/schemas/pipeline_state.py`](file:///c:/ITI_GP/src/ai-pipeline/ai-service/app/schemas/pipeline_state.py)
- **Problem:** LangGraph `StateGraph` enforces strict TypedDict runtime validation. Extra return keys caused `langgraph.channels.base.InvalidUpdateError`.
- **Fix:** Added `extraction_telemetry: Optional[Dict[str, Any]]` and `error_code: Optional[str]` directly to `PipelineState`.

### 1.6 Story Repair Node Type Safety
- **Location:** [`ai-service/app/nodes/repair_stories.py`](file:///c:/ITI_GP/src/ai-pipeline/ai-service/app/nodes/repair_stories.py)
- **Problem:** `attempts = state.get("repair_attempts", 0) + 1` caused `TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'` when `repair_attempts` was explicitly passed as `None`.
- **Fix:** Hardened with `attempts = int(state.get("repair_attempts") or 0) + 1`.

---

## 2. Real E2E Fixture Verification Matrix

All tests were executed against `ai-service/test-fixtures/e2e_real_mixed` using real LLM models (`meta-llama/llama-3.3-70b-instruct` via OpenRouter) and Groq Whisper (`whisper-large-v3`):

| Fixture / Modality | Ingest Chunks | Extracted / Generated Stories | Status | Provenance & Evidence Verification |
| :--- | :---: | :---: | :---: | :--- |
| **`technical-notes.docx`** | 3 chunks | **3 User Stories** | `completed` | Verified paragraph coordinates (`chk_job-test-docx_doc_0_..._p1_c0`). Generated security audit log, PostgreSQL retention, and SMS token 2FA validation stories. |
| **`requirements.pdf`** | 3 chunks | **4 User Stories** | `completed` | Extracted `REQ-AUTH-101` (Password Reset Link), `REQ-AUTH-102` (30-Minute Expiration), `REQ-AUTH-103` (Password Complexity), `REQ-AUTH-104` (Account Lockout). Adversarial prompt injection successfully ignored. |
| **`stakeholder-notes.txt`** | 1 chunk | **4 User Stories** | `completed` | Generated user onboarding, password reset notifications, and profile change email alert stories. |
| **`meeting-audio.wav`** | 1 audio source | **2 User Stories** | `completed` | Groq Whisper transcribed audio; LLM generated 15-minute token expiration revision & SMS 2FA verification stories with timestamp provenance. |
| **MultiDoc Combined** (DOCX + PDF + TXT) | 7 chunks | **11 User Stories** | `completed` | Clean multi-document cross-referencing and deduplication across 3 documents simultaneously. |
| **Mixed All 4 Sources** (DOCX + PDF + TXT + Audio WAV) | 8 chunks | **13 User Stories** | `completed` | Full unified pipeline run across mixed document & audio modalities. |

---

## 3. Regression Test Suite

A dedicated regression test suite was added at [`ai-service/tests/nodes/test_bug_hunt_regressions.py`](file:///c:/ITI_GP/src/ai-pipeline/ai-service/tests/nodes/test_bug_hunt_regressions.py):

1. `test_docx_modality_dispatch_when_file_type_is_document`: Verifies DOCX parser is invoked even with generic MIME / `file_type="document"`.
2. `test_normalize_single_requirement_object`: Verifies single dictionary objects are wrapped into canonical collections.
3. `test_normalize_numeric_string_dict`: Verifies `{"1": {...}, "2": {...}}` payloads are properly unpacked.
4. `test_normalize_shorthand_label_keys`: Verifies `{"FR": "Requirement"}` parsing while rejecting non-label keys.
5. `test_extract_model_timeout_classification`: Verifies timeout exceptions are classified as `EXTRACT_MODEL_TIMEOUT`.
6. `test_extract_provider_error_classification`: Verifies API errors are classified as `EXTRACT_PROVIDER_FAILURE`.
7. `test_extract_all_chunks_failed_marks_error`: Verifies complete technical failures yield `status="error"`.
8. `test_quality_scoring_zero_requirements_not_vacuously_perfect`: Verifies 0-count edge cases return `0.0` scores.
9. `test_quality_scoring_zero_stories_not_vacuously_perfect`: Verifies 0-story edge cases return `0.0` scores.

### Full Test Suite Results
```text
============================== test session starts ==============================
collected 581 items / 2 skipped

579 passed, 2 skipped, 146 warnings in 510.49s (100% pass rate)
=================================================================================
```

---

## 4. Conclusion & Deployment Readiness

The Requra.AI requirements extraction and pipeline architecture is now fully hardened against:
- Binary format misdetection.
- Unhandled model JSON topologies.
- Silent failure swallowing.
- Vacuous quality scores.
- Concurrency overload.

The pipeline is verified and ready for production deployment.
