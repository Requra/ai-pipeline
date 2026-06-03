# Transcribe Node (`transcribe_if_audio`)

## Current Status
- Fully implemented inside `app/nodes/transcribe.py`.
- Supports mp3/wav/ogg files.
- Calls Groq Whisper large-v3 with fallback to Deepgram Nova-3.
- Features dual-run alignment logic for bilingual Egyptian Arabic / English meeting recordings.

## Current Problems
- Returns a flat string, discarding speaker labels and timeline offsets.
- Does not preserve structured segments for downstream citations, preventing grounding.
- Emits stdout logging print statements instead of structured logs.

## Production Target
- Return structured transcription chunks that retain speaker IDs and time segments.
- Allow downstream requirements to cite exact source locations.

## Planned Changes / Enhancements
1. **Source Chunks List output**:
   - **What changes**: Modify the return type to populate a list of `SourceChunk` items directly into the pipeline state instead of a flat string `raw_text`.
   - **Why it changes**: Preserves speaker and time segment boundaries, allowing downstream requirements to cite exact sources.
   - **Where it will likely be implemented**: `app/nodes/transcribe.py` (main output formatter).
   - **Input impact**: `raw_bytes: bytes`.
   - **Output impact**: `chunks: List[SourceChunk]`.
   - **Validation impact**: Verified by checking that outputs validate against Pydantic models.
   - **Risk**: API structures returned from Whisper or Deepgram may change format. Mitigated by using strict Pydantic parsing wrappers.
2. **Runtime Ffmpeg Verification**:
   - **What changes**: Add a check at node execution start verifying that `ffmpeg` is available on the system path.
   - **Why it changes**: Ffmpeg is required for audio compression and chunking; missing packages trigger silent pipeline halts.
   - **Where it will likely be implemented**: `app/nodes/transcribe.py` init checks.
   - **Input impact**: None.
   - **Output impact**: None.
   - **Validation impact**: Checked in system package tests.
   - **Risk**: Small delay at start.
3. **Structured logging Telemetry**:
   - **What changes**: Replace stdout print statements with structured JSON log events.
   - **Why it changes**: Adheres to the strict logging guidelines.
   - **Where it will likely be implemented**: All log points.
   - **Input impact**: state fields.
   - **Output impact**: Telemetry metrics.
   - **Validation impact**: Checked in observability audits.
   - **Risk**: Minor log volume increase.

## Input Contract
- `raw_bytes`: `bytes`
- `file_type`: `str` (must be `"audio"`)
- `audio_format`: `str` (default `"mp3"`)
- `language`: `str` (default `"ar"`)

## Output Contract
- `chunks`: `List[SourceChunk]`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- On primary transcoder API timeout, invoke the secondary fallback. If all APIs fail, populate the `error` field with `"TRANSCRIBE_FALLBACK_FAILURE: <details>"` and route to `contract_formatter`, setting status to `error`. Never return flat transcript mockups.

## Routing Behavior
- Route to `parse_to_chunks` on successful completion.
- Route to `contract_formatter` on failure of all providers.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_size` (bytes), `output_count` (chunks generated), `warning_count`, `error_code`, `provider`, `model`, `estimated_cost`.

## Tests Required
- **Unit Test**: Verify file splitting logic for files exceeding 24MB.
- **Integration Test**: Check fallback execution under simulated API connection drops.
- **Failure-Path Test**: Verify correct transition to `contract_formatter` when both Groq and Deepgram are unavailable.

## Acceptance Criteria
- [ ] Returns list of `SourceChunk` structures containing speaker IDs and timeline segments.
- [ ] Fallback mechanism resolves successfully under connection timeouts.
- [ ] Structured logging is active.
- [ ] No flat transcript-only production output is generated.

## Meeting Notes
- Review dual-run performance metrics to determine if the bilingual merge should remain default for meeting recordings.
