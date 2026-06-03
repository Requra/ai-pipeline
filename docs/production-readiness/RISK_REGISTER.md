# ⚠️ Production Risk Register (Requra.AI Pipeline)

This document tracks identified architectural, operational, and model risks associated with the Requra.AI pipeline along with mitigation actions.

---

## 1. Technical & Model Risks

### Risk 1: Requirement Hallucination
* **Description**: The LLM extracts plausible-sounding requirements that are not present in the original document.
* **Likelihood**: Medium
* **Impact**: Critical (degrades pipeline integrity and trust).
* **Mitigation**: Implement `evidence_grounding_node` to mathematically cross-examine requirements and stories back to source chunks, filtering ungrounded entries.

### Risk 2: Infinite Self-Repair Loops
* **Description**: The quality gate node repeatedly rejects corrected items, leading to execution loops between `quality_gate` and `repair`.
* **Likelihood**: Low
* **Impact**: High (increases API costs and transaction timeouts).
* **Mitigation**: Implement a loop counter in `PipelineState` that terminates repair iterations after 2 cycles, routing the state to `needs_review` status.

### Risk 3: API Service Outages
* **Description**: Third-party providers (Google Gemini, Groq, Deepgram) encounter outages.
* **Likelihood**: Medium
* **Impact**: High (blocks pipeline execution).
* **Mitigation**: Implement automatic retries on transient errors and establish provider fallbacks (e.g., transcription switches from Groq to Deepgram).

---

## 2. Security & Compliance Risks

### Risk 4: PII Data Leaks
* **Description**: Customer documents contain sensitive PII (names, phone numbers, emails) which are transmitted to third-party APIs.
* **Likelihood**: High
* **Impact**: Critical (violates compliance regulations like GDPR and HIPAA).
* **Mitigation**: Keep regex-based masking inside the `ingest` node, sanitizing input before sending data to external APIs.

### Risk 5: Denials of Service (DoS) via File Size
* **Description**: Users upload excessively large documents or audio streams, blocking resources.
* **Likelihood**: Medium
* **Impact**: Medium
* **Mitigation**: Establish file limits in `detect_file_type` (e.g. 50MB for audio, 20MB for PDFs) and limit page counts.

---

## 3. Operational & Cost Risks

### Risk 6: Exploding API Expenses
* **Description**: Highly concurrent chunk-by-chunk processing significantly increases model costs.
* **Likelihood**: Medium
* **Impact**: Medium
* **Mitigation**: Cache identical chunk outputs, track execution metrics in structured logs, and use Gemini 1.5 Flash.

### Risk 7: Missing System Dependencies
* **Description**: Missing system utilities like `ffmpeg` on target host environments crash the transcoder node.
* **Likelihood**: High
* **Impact**: High
* **Mitigation**: Verify the presence of system executables at boot, logging clear environment failures.
