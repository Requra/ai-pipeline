# Ingest Node (`ingest`)

## Current Status
- Fully implemented inside `app/nodes/ingest.py`.
- Integrates `PyMuPDF` (`fitz`) and `python-docx` for extracting text from raw upload bytes.
- Implements basic PII masking and whitespace normalization.

## Current Problems
- Couples multiple operations (parsing, text extraction, relevance checking, PII masking).
- If the relevance LLM check fails or is slow, the entire ingestion step blocks, even if extraction was successful.
- Does not preserve original page, paragraph, or segment boundaries.

## Production Target
- Standardizes extraction inputs, masks PII, and handles basic format validation.
- Moves magic-byte type checking, text chunking, and relevance checks to downstream nodes.

## Planned Changes / Enhancements
1. **Decouple relevance checking and chunking**:
   - **What changes**: Remove the relevance check prompts and splitting loops from the ingest node function.
   - **Why it changes**: Follows the single-responsibility principle, improving failure isolation and testing.
   - **Where it will likely be implemented**: `app/nodes/ingest.py` (removing sections).
   - **Input impact**: `raw_bytes: bytes`.
   - **Output impact**: Outputs raw unchunked text instead of lists of requirements.
   - **Validation impact**: Checked by compiling the modified ingest node functions.
   - **Risk**: Pipeline breaking due to downstream missing variables. Mitigated by updating pipeline state flow definitions.
2. **Standardize Whitespace Normalization**:
   - **What changes**: Implement a clean carriage return (`\r\n` -> `\n`) and space deduplication formatter.
   - **Why it changes**: Prevents parsing errors in downstream tokenizers and matching tools.
   - **Where it will likely be implemented**: `app/nodes/ingest.py` helpers.
   - **Input impact**: state fields.
   - **Output impact**: Cleaned text string.
   - **Validation impact**: Verified by testing text with varying spaces and line boundaries.
   - **Risk**: Accidental deletion of formatting tabs in data tables. Mitigated by testing tables layout.
3. **PII Masking Integration**:
   - **What changes**: Verify that basic phone number and email regexes run on the extracted raw text string.
   - **Why it changes**: Protects sensitive user identities before sending data to third-party endpoints.
   - **Where it will likely be implemented**: `app/nodes/ingest.py` regex helpers.
   - **Input impact**: raw text.
   - **Output impact**: Masked text string.
   - **Validation impact**: Verified by checking that sample PII details are replaced by tags (e.g. `[EMAIL]`).
   - **Risk**: Regex false positives masking legitimate technical data (like numbers matching phone patterns). Mitigated by tuning number matching criteria.

## Input Contract
- `job_id`: `str`
- `raw_bytes`: `bytes`
- `file_type`: `str` (determined by `detect_file_type`)
- `metadata`: `Dict[str, Any]`

## Output Contract
- `raw_text`: `str`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- If bytes are empty or format parsing throws exceptions, populate `error` with `"INGEST_FAILED: <details>"` and route directly to `contract_formatter` using LangGraph command routing, setting status to `error`. Never return fake extracted text.

## Routing Behavior
- If `file_type == "audio"`, route to `transcribe_if_audio`.
- If `file_type != "audio"` and no errors, route to `parse_to_chunks`.
- On error, route to `contract_formatter`.

## Observability
- Emits structured log event containing: `job_id`, `trace_id`, `node_name`, `status`, `input_size` (size of input bytes), `duration_ms`, `warning_count`, `error_code`.

## Tests Required
- **Unit Test**: Test PII masking with sample emails and phone numbers.
- **Integration Test**: Verify binary stream parsing with simple PDF and DOCX samples.
- **Failure-Path Test**: Verify correct routing and error population on empty payloads.

## Acceptance Criteria
- [ ] Relevance checks are completely removed.
- [ ] PII masking preserves text structure while hiding emails and phone numbers.
- [ ] Errors gracefully route directly to the contract formatter.

## Meeting Notes
- Discuss with the team if there are any specific PII masking patterns (e.g. Arabic names or government IDs) that need special rules.
