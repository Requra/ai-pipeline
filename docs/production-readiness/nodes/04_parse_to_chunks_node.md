# Parse to Chunks Node (`parse_to_chunks`)

## Current Status
- New Node. Text splitting is currently handled inside `extract.py` using `chunk_text_by_words()` which splits text into exactly 5 equal parts.

## Current Problems
- Splitting text into exactly 5 equal parts cuts sentences and paragraphs in half, discarding structural context.
- Discards page numbers and paragraph markers, which prevents downstream grounding verification.

## Production Target
- Parse text into overlapping chunks, maintaining page, paragraph, or speaker time coordinates in a list of `SourceChunk` models.

## Planned Changes / Enhancements
1. **Source-Aware Chunking Algorithms**:
   - **What changes**: Implement format-specific segmenting algorithms:
     - **PDF**: Page-by-page extraction, creating a `SourceChunk` per page with page numbers.
     - **DOCX**: Extract by paragraph/table boundary, keeping paragraph positions.
     - **Audio**: Group segments by speaker turns or time slots (e.g. 5 minutes).
     - **Text/Fallback**: Overlapping token splitting (e.g. 500 tokens with 50 token overlap).
   - **Why it changes**: Preserves structural coordinates to make grounding citations possible.
   - **Where it will likely be implemented**: `app/nodes/parse_to_chunks.py`.
   - **Input impact**: `raw_text: str` or `chunks: List[SourceChunk]` (from transcription).
   - **Output impact**: `chunks: List[SourceChunk]`.
   - **Validation impact**: Verified by testing page boundary mappings on PDFs.
   - **Risk**: Table layout parsing inside DOCX/PDF. Mitigated by using standard table extractors (e.g. python-docx tables loop).
2. **Metadata Coordinate tracking**:
   - **What changes**: Generate a unique `chunk_id` for every parsed block (e.g., `chk_{job_id}_{page}_{index}`) and track start/end character offsets inside the raw text.
   - **Why it changes**: Guarantees distinct tracking identifiers for downstream verification logs.
   - **Where it will likely be implemented**: `app/nodes/parse_to_chunks.py`.
   - **Input impact**: state fields.
   - **Output impact**: `chunks: List[SourceChunk]`.
   - **Validation impact**: Checked by tracking character offsets in unit tests.
   - **Risk**: Offset mapping errors when PII is masked. Mitigated by tracking indices after PII normalization.
3. **No 5-Equal-Word Splitting**:
   - **What changes**: Completely remove the naive `chunk_text_by_words` logic that segments documents into exactly 5 files.
   - **Why it changes**: Equal-word splitting cuts sentences, breaking business logic context for LLMs.
   - **Where it will likely be implemented**: `app/nodes/parse_to_chunks.py` (replacing legacy code).
   - **Input impact**: Raw text.
   - **Output impact**: Standard token/page-based chunks.
   - **Validation impact**: Checked during integration testing.
   - **Risk**: Large document parsing costs. Mitigated by establishing a maximum chunk list limit.

## Input Contract
- `raw_text`: `Optional[str]`
- `file_type`: `str`
- `raw_bytes`: `bytes`
- `chunks`: `List[SourceChunk]` (pre-populated if audio transcription ran)

## Output Contract
- `chunks`: `List[SourceChunk]`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- If text is missing or parsing throws exceptions, populate `error` with `"CHUNK_FAILED: <details>"`, set status to `error`, and route directly to `contract_formatter`. Never return mock chunk arrays.

## Routing Behavior
- Route to `relevance_check` on successful completion.
- Route to `contract_formatter` on errors.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_size` (text length), `output_count` (number of chunks), `warning_count`, `error_code`.

## Tests Required
- **Unit Test**: Test page mapping for multi-page PDF documents.
- **Integration Test**: Verify overlapping token segment boundaries.
- **Failure-Path Test**: Verify correct transition to contract formatter on parsing exceptions.

## Acceptance Criteria
- [ ] Every chunk contains correct page number or paragraph metadata.
- [ ] Overlapping chunks do not cut words or sentences in half.
- [ ] Discards the naive 5-equal-word splitting algorithm.
- [ ] Audio chunks maintain speaker and timestamp metadata.

## Meeting Notes
- Establish the target chunk size (in tokens) to balance extraction coverage and API costs.
