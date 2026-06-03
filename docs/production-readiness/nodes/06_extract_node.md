# Extract Node (`extract_requirements`)

## Current Status
- Implemented inside `app/nodes/extract.py`.
- Uses Gemini to extract a list of functional requirements.
- Uses a hardcoded mock fallback if the LLM fails.

## Current Problems
- Focuses only on functional requirements (FRs), completely missing Non-Functional Requirements (NFRs), Business Rules (BRs), constraints, assumptions, open questions, and out-of-scope declarations.
- Using hardcoded fallbacks in production risks outputting fake requirements, which is strictly prohibited.
- Naively outputs `classified_requirements` instead of raw `extracted_requirements`, blurring extraction and classification steps.

## Production Target
- Extract multiple requirements (FR, NFR, BR, Constraints, Assumptions, Open Questions, Out-of-Scope) chunk-by-chunk in parallel.
- Maintain source chunk mappings, enforce non-empty evidence quotes, and output `extracted_requirements`.

## Planned Changes / Enhancements
1. **Output Split Definition**:
   - **What changes**: Node output will populate `extracted_requirements` inside `PipelineState`, rather than `classified_requirements` directly.
   - **Why it changes**: Establishes a clear separation of concerns; extraction isolates candidate entities, and classification verifies label types and confidences.
   - **Where it will likely be implemented**: `app/nodes/extract.py`.
   - **Input impact**: `chunks: List[SourceChunk]`.
   - **Output impact**: `extracted_requirements: List[ExtractedRequirement]`.
   - **Validation impact**: Unit tests check `ExtractedRequirement` models validate.
   - **Risk**: Changes schema fields downstream. Mitigated by updating all subsequent node inputs.
2. **Expanded Category Scope**:
   - **What changes**: Prompt and schema are updated to extract 7 categories: Functional (FR), Non-Functional (NFR), Business Rules (BR), Constraints, Assumptions, Open Questions, and Out-of-Scope.
   - **Why it changes**: Ensures capture of non-functional constraints and client questions from raw text or transcripts.
   - **Where it will likely be implemented**: `app/nodes/extract.py` prompts.
   - **Input impact**: Chunks.
   - **Output impact**: Extracted requirements with candidate labels.
   - **Validation impact**: Verified against mock documents containing out-of-scope annotations.
   - **Risk**: Categorization noise. Handled by classification thresholding.
3. **Mandatory Evidence Grounding**:
   - **What changes**: Enforce that every extracted requirement contains a non-empty `evidence` quote list (`len(evidence) >= 1`).
   - **Why it changes**: Prevents hallucinated items from leaving the node.
   - **Where it will likely be implemented**: Pydantic schema validation on extraction outputs.
   - **Input impact**: Chunks.
   - **Output impact**: Grounded candidate requirements.
   - **Validation impact**: Checked during parsing validation.
   - **Risk**: Minor parsing variations might drop valid items. Mitigated by returning flags.
4. **No Hallucinated Fallbacks**:
   - **What changes**: Remove hardcoded fallback requirements entirely. On failure, return an empty list and raise state warnings/errors.
   - **Why it changes**: Production rules strictly prohibit fake fallback generation.
   - **Where it will likely be implemented**: Extraction error handling.
   - **Input impact**: Chunks.
   - **Output impact**: Empty lists with error/warning tags.
   - **Validation impact**: Checked during API error tests.
   - **Risk**: Downstream nodes must handle empty lists safely. Handled by graph routing checks.

## Input Contract
- `chunks`: `List[SourceChunk]`
- `job_id`: `str`

## Output Contract
- `extracted_requirements`: `List[ExtractedRequirement]`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- If LLM processing fails, return an empty list and populate `error` with `"EXTRACT_FAILED: <details>"`. Never invent fake candidate requirements.

## Routing Behavior
- Route to `classify_requirements` on success or partial output.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_size` (chunks), `output_count` (requirements), `warning_count`, `error_code`, `provider`, `model`.

## Tests Required
- **Unit Test**: Test chunk parsing and candidate category extraction.
- **Integration Test**: Check concurrent processing over multiple chunks.
- **Failure-Path Test**: Verify execution continues with an empty list if model requests time out.

## Acceptance Criteria
- [ ] Requirements are extracted with matching non-empty evidence quotes.
- [ ] All 7 target category types are supported.
- [ ] No fake mock requirements are generated on failure.

## Meeting Notes
- Standardize the target agile user story format for NFRs, Business Rules, and Out-of-Scope items.
