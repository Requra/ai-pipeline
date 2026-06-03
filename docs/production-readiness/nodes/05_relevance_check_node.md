# Relevance Check Node (`relevance_check`)

## Current Status
- New Node. Previously, the relevance check was tightly coupled inside the ingestion node, checking only the first 2,000 characters.

## Current Problems
- Ingestion checks only the start of the document. Relevant requirements buried deep inside long files (e.g. page 10 of a PDF) were silently skipped.
- Lacks a mechanism to flag ambiguous documents, either rejecting them outright or passing them blindly.

## Production Target
- Evaluate multiple chunks spread throughout the document to determine relevance.
- Route irrelevant files directly to the final contract formatter with a rejected status.
- Flag ambiguous documents for manual review rather than discarding them silently.

## Planned Changes / Enhancements
1. **Multi-Chunk Sampling**:
   - **What changes**: The node will inspect chunks from the beginning, middle, and end of the document rather than a simple characters prefix.
   - **Why it changes**: Prevents missing requirements in large documents.
   - **Where it will likely be implemented**: `app/nodes/relevance_check.py`.
   - **Input impact**: `chunks: List[SourceChunk]`.
   - **Output impact**: Updates `is_useful` and `relevance_score`.
   - **Validation impact**: Verified by testing multi-page PDFs with buried tables.
   - **Risk**: Increased API cost if checking too many chunks. Managed by setting a max chunk check threshold (e.g., max 5 sampled chunks).
2. **Relevance Logic Upgrade**:
   - **What changes**: Combine a keyword-based heuristic check with structured LLM validation.
   - **Why it changes**: Ensures fast path shortcutting for obviously irrelevant files while remaining highly accurate.
   - **Where it will likely be implemented**: `app/nodes/relevance_check.py`.
   - **Input impact**: state fields.
   - **Output impact**: `relevance_score`, `is_useful`.
   - **Validation impact**: Unit tests verify keyword hits.
   - **Risk**: LLM parsing timeouts. Mitigated by fallback heuristics.
3. **Ambiguity Gating**:
   - **What changes**: If `relevance_score` is between 0.40 and 0.65, set `status = "needs_review"` but keep `is_useful = True` to allow extraction.
   - **Why it changes**: Ensures marginal documents are parsed but marked for PM verification.
   - **Where it will likely be implemented**: `app/nodes/relevance_check.py`.
   - **Input impact**: state fields.
   - **Output impact**: `status = "needs_review"`.
   - **Validation impact**: Verified by testing low-confidence requirements documentation.
   - **Risk**: Marginally increases review queue size.

## Input Contract
- `chunks`: `List[SourceChunk]`
- `job_id`: `str`

## Output Contract
- `is_useful`: `bool`
- `relevance_score`: `float`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- If LLM checks fail, execute the keyword-based heuristic fallback. If both fail, log a warning and default to `is_useful = True` with `relevance_score = 0.5` and set `status = "needs_review"` to avoid silently breaking the pipeline.

## Routing Behavior
- If `is_useful == True` and `status == "needs_review"`, route to `extract_requirements`.
- If `is_useful == True` and `status != "needs_review"`, route to `extract_requirements`.
- If `is_useful == False`, route directly to `contract_formatter` with status `rejected`.

## Observability
- Emits structured log event containing: `job_id`, `trace_id`, `node_name`, `status`, `relevance_score`, `input_size` (number of chunks checked), `duration_ms`.

## Tests Required
- **Unit Test**: Test keyword heuristics with relevant and irrelevant texts.
- **Integration Test**: Check graph routing bypass when irrelevant files are provided.
- **Failure-Path Test**: Verify fallback to heuristic evaluation when Gemini times out.

## Acceptance Criteria
- [ ] Relevance evaluations are performed across multiple chunks.
- [ ] Irrelevant files route directly to the contract formatter.
- [ ] Ambiguous documents are flagged as `needs_review` and continue extraction.

## Meeting Notes
- Review the list of relevance keywords to ensure it aligns with business specifications.
