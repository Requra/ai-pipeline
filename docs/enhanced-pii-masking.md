# Technical Implementation Walkthrough: Enhanced PII Masking

We have implemented the **Enhanced PII Masking (Gap 15)** feature to scrub highly sensitive secrets and financial data (API keys, credit cards, cloud credentials) during document ingestion, preventing accidental leakage to external LLM providers.

---

## 1. Design & Architecture

### 1.1 Scope of Masking
To preserve semantic business actor context for requirements extraction (e.g., "Product Owner Sarah approved the workflow"), we intentionally avoid aggressive name, address, or location scrubbing. Masking is restricted to high-confidence patterns:

1. **Emails**: Replaced with `[EMAIL]`.
2. **Phone Numbers**: Replaced with `[PHONE]` (restricted to digit sequences of 7–15 digits to avoid number overlaps).
3. **Credit Cards**: Validated via regex *and* Luhn algorithm checksum verification; valid cards are replaced with `[CREDIT_CARD]`.
4. **API Keys**: High-confidence patterns for cloud and AI providers:
   - **OpenAI**: `sk-[a-zA-Z0-9]{32,}` or `sk-proj-[a-zA-Z0-9_]{32,}`
   - **AWS**: `AKIA[A-Z0-9]{16}` or `ASCA[A-Z0-9]{16}`
   - **GitHub**: `ghp_[a-zA-Z0-9]{36}`
   - **Hugging Face**: `hf_[a-zA-Z0-9]{34}`
   - **Google API**: `AIzaSy[a-zA-Z0-9_\-]{33}`
   - **Generic Secrets**: Key-value pairs matching `api_key = ...` or `db_password = ...` with at least 16 characters.

### 1.2 Flow Control & Telemetry
* **Configuration Toggle**: Introduced `ENABLE_PII_MASKING` (defaulting to `true`). If set to `false` (e.g. during local debugging), masking is entirely bypassed.
* **Safe Statistics Logging**: Matches are counted per category and logged as counts (e.g., `{"api_keys": 1, "credit_cards": 1}`). The raw characters are never printed to the logs.
* **State Tracking**: Masking statistics are stored internally inside the `PipelineState` (as `pii_stats`) but **not** included in the final formatted response payload to ensure 100% backward-compatibility for backend services.

---

## 2. Key Components & File Changes

* **[config.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/config.py)**:
  - Added the `ENABLE_PII_MASKING` configuration field.
* **[pipeline_state.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/schemas/pipeline_state.py) & [state.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/worker/state.py)**:
  - Declared and initialized `pii_stats` on the internal state container.
* **[ingest.py](file:///d:/ITI/GP/ai-pipeline/ai-service/app/nodes/ingest.py)**:
  - Implemented the `_is_luhn_valid` helper function.
  - Overhauled `_mask_pii` to match provider credentials, validate credit cards, compute statistics, and return `(masked_text, stats)`.
  - Updated `ingest_node` to execute masking conditionally, log stats, and populate `pii_stats`.

---

## 3. Verification Results

### 3.1 Unit Testing
Added comprehensive tests in [test_ingest.py](file:///d:/ITI/GP/ai-pipeline/ai-service/tests/nodes/test_ingest.py):
1. **Luhn Credit Card Validation**: Verified that Luhn-valid cards are masked while Luhn-invalid cards pass through.
2. **Provider Key Matching**: Verified specific regex matches for OpenAI, AWS, GitHub, Google API, and Hugging Face keys.
3. **False Positive Prevention**: Verified that standard numbers (quantities, transaction IDs, currency costs) are not accidentally masked.
4. **Config Toggle**: Verified that setting `ENABLE_PII_MASKING=False` disables masking.
5. **State Propagation**: Verified that `pii_stats` maps correct category counts.

Run the test suite:
- **Result**: `316 passed` (100% green).
