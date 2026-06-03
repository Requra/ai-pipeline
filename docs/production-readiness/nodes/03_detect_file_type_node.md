# Detect File Type Node (`detect_file_type`)

## Current Status
- New Node. Currently, the file type is passed directly by the client in the API request, and ingestion trusts it blindly.

## Current Problems
- Clients can upload mismarked files (e.g. uploading audio labeled as a PDF), causing downstream parser crashes.
- Lacks safety rails for unsupported file formats.
- No verification of empty uploads.

## Production Target
- Inspect binary byte streams using MIME magic bytes to determine the correct parser, outputting `DocumentSource` metadata.
- Enforce size limits and format checks without trusting the client parameters.

## Planned Changes / Enhancements
1. **Magic Byte / MIME Detection**:
   - **What changes**: Use python-magic or simple byte prefix checking to inspect the raw file headers (e.g. `%PDF-` for PDFs, `PK` for DOCX, standard tags for MP3/WAV) rather than using the frontend-supplied parameter.
   - **Why it changes**: Prevents parser crashes on incorrectly labeled files.
   - **Where it will likely be implemented**: `app/nodes/detect_file_type.py`.
   - **Input impact**: `raw_bytes: bytes`.
   - **Output impact**: `file_type: str`, `source_metadata: DocumentSource`.
   - **Validation impact**: Verified by passing files with mismatched extensions.
   - **Risk**: Library installation errors (e.g. libmagic DLLs on Windows). Mitigated by implementing a simple pure-python magic byte fallback header check.
2. **File Size and Empty Checks**:
   - **What changes**: Programmatically reject files that are empty (`len(raw_bytes) == 0`) or exceed set limits (20MB for documents, 50MB for audio).
   - **Why it changes**: Prevents Denials of Service (DoS) and out-of-memory errors on the application container.
   - **Where it will likely be implemented**: `app/nodes/detect_file_type.py`.
   - **Input impact**: state fields.
   - **Output impact**: `error` populated on violation.
   - **Validation impact**: Verified by testing large files.
   - **Risk**: Legitimate large audio files getting rejected. Mitigated by documenting bounds clearly.
3. **MIME Metadata Compilation**:
   - **What changes**: Node will generate a `DocumentSource` Pydantic model containing: `filename`, `file_size_bytes`, `mime_type`, `sha256_hash`, and populate it in state.
   - **Why it changes**: Provides metadata tracking for logging and caching.
   - **Where it will likely be implemented**: `app/nodes/detect_file_type.py`.
   - **Input impact**: state fields.
   - **Output impact**: `source_metadata: DocumentSource`.
   - **Validation impact**: Checked against target Pydantic schemas.
   - **Risk**: Metadata hashing computation latency. Mitigated by using fast hash algorithms (e.g., hashlib.sha256).

## Input Contract
- `raw_bytes`: `bytes`
- `metadata`: `Dict[str, Any]`

## Output Contract
- `file_type`: `Literal["pdf", "docx", "audio", "text"]`
- `source_metadata`: `DocumentSource`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- If bytes are empty, files exceed size limits, or format is unsupported, set `error` to `"FILE_TYPE_REJECTED: <reason>"`, set status to `rejected`, and route directly to `contract_formatter`. Never make baseline assumptions or fallback to default formats.

## Routing Behavior
- Route to `ingest` node on successful format verification.
- Route to `contract_formatter` on validation failure.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_size` (bytes), `output_count` (1), `warning_count`, `error_code`.

## Tests Required
- **Unit Test**: Test MIME magic byte detection with valid and invalid sample files (PDF, DOCX, MP3, WAV).
- **Contract Test**: Ensure proper errors are returned for empty payloads.
- **Failure-Path Test**: Verify size limit rejections route to the contract formatter.

## Acceptance Criteria
- [ ] MIME detection determines the format without relying on file extensions or client parameters.
- [ ] Rejects files exceeding size limits.
- [ ] Outputs a valid `DocumentSource` schema object.
- [ ] No trust is given to frontend-supplied file types.

## Meeting Notes
- Define list of supported audio formats (MP3, WAV, OGG, M4A) and establish limits.
