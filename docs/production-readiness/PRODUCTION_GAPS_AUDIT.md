# 🔍 Production Gaps Audit (Requra.AI Pipeline)

This document presents the detailed architectural and operational gaps identified in the current branch (`review/full-pipeline-merge`) of `Requra/ai-pipeline`. Each gap lists the issue, business/architectural risk, and proposed solution.

---

## 1. Foundation & Dependencies Gaps
- **Missing Production Dependency Structure**: The `pyproject.toml` file in `ai-service/` lacks strict production pinning for critical libraries (e.g. PyMuPDF, python-docx, groq, pydantic, and uvicorn). It relies on local dev environments without lockfiles or specific version ranges.
- **Docker & System Package Gaps**: The Dockerfile lacks explicit system packages required for audio and document processing. Specifically, `ffmpeg` and `libmagic` are missing, which will cause immediate runtime failures in staging/production for audio processing or MIME-based detection.
- **Environment Verification**: There is no startup configuration validation. If providers like Google Gemini, Groq, or Deepgram are missing keys, the application boots and fails only during active requests, wasting server cycles.

---

## 2. Ingestion & File Type Detection Gaps
- **Ambiguous Ingestion Routing**: In `app/nodes/ingest.py`, file type is passed directly from the client via the frontend `file_type` parameter. This is a vector for crashes if the user uploads an MP3 file labeled as a PDF, or uploads an unsupported format.
- **Lack of True File Type Detection**: There is no server-side MIME type or magic byte parsing to verify the file format before starting downstream extraction.
- **Monolithic Ingest Logic**: The smart filter, text extraction, PII masking, and relevance checks are all coupled inside `ingest.py`. If one of these stages fails, the entire ingestion collapses.

---

## 3. Parsing & Chunking Gaps
- **Naïve Chunking Strategy**: The extract node chunks text in `app/nodes/extract.py` using `chunk_text_by_words()` which splits the document into exactly 5 equal word-based parts. This breaks paragraphs, cuts sentences in half, and ignores document structure.
- **Source Metadata Loss**: Chunks do not preserve original page numbers (PDF), paragraph IDs (DOCX), or timestamps/speaker labels (audio). This makes traceability and grounding impossible.

---

## 4. Transcription Hardening Gaps
- **No Speaker or Timestamp Propagation**: Although `transcribe.py` has advanced bilingual merging logic, it returns a plain text string `raw_text` to the state. The speaker tags (`**[Speaker X]**`) are lost during extraction, preventing the system from citing *who* said *what* during client meetings.
- **Provider Failover Fragility**: While failover between Groq and Deepgram is coded, it handles error logs via stdout `print()` statements. In production, this prevents structured APM alerting.

---

## 5. Requirement Extraction & Classification Gaps
- **Functional-Only Focus**: The current `extract.py` is hardcoded to extract only functional requirements. Non-functional requirements (security, latency, scalability) and business rules (permissions, policies) are completely missed or categorized poorly.
- **Hallucination Fallback Vulnerability**: When the LLM fails, `extract.py` returns a mock fallback requirement (`The system shall allow users to browse products.`). In production, generating fake requirements is a severe quality violation.
- **Naïve Deduplication**: Deduplication is performed by checking only the lowercased combination of `(actor, goal)`. If two requirements share an actor and goal but specify different behaviors, one is silently deleted.

---

## 6. User Story Generation Gaps
- **Weak Acceptance Criteria Validation**: The `generate.py` node converts requirements into user stories but performs zero structure check. If the LLM generates a story without "Given-When-Then" fields or outputs a plain text summary, it is passed downstream without correction.
- **Agile Cardinality Limits**: The current codebase only supports a hardcoded 1 requirement -> 1 story relationship, failing to support one-to-many, many-to-one, attached-as-criteria, non-story, and needs_review mappings.
- **Hallucinated Story Fallbacks**: Similar to extraction, a hardcoded fallback user story is generated if the LLM fails.

---

## 7. Missing Quality & Grounding Nodes (Crucial Gaps)
- **No Evidence Grounding Node**: There is no verification that extracted requirements exist in the original text. Hallucinated items are passed to the database. Requirements must require a non-empty evidence list for all production runs.
- **No Quality Gate Node**: There is no automated inspection of the output stories for missing actors, empty acceptance criteria, bad labels, or low-confidence classification.
- **No Repair Node**: When a quality gate failure is detected, the pipeline has no loop back to repair the items, forcing manual review for fixable issues.

---

## 8. Formatter & Output Contract Gaps
- **Unstable Response Contract**: The FastAPI `app/main.py` directly returns the pipeline state dictionary (minus `raw_bytes`). This exposes internal variables (like `is_useful`, `relevance_score`, `started_at`, and raw extraction metrics) rather than presenting a stable `JobResult` contract.
- **No Export Formatter**: The system lacks mechanisms to export outputs into Jira-ready tables, CSV, or Excel formats.

---

## 9. Observability & Change Records Gaps
- **Standard Output Logs**: Logging is performed using `print()` and standard Python `logging` without trace correlation. There is no `trace_id` or `job_id` embedded in logs.
- **No Token or Cost Tracking**: The service makes expensive LLM and transcription calls without tracking prompt/completion tokens or calculating estimated costs, making cost allocation impossible.
