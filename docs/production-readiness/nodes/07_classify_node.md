# Classify Node (`classify_requirements`)

## Current Status
- Implemented inside `app/nodes/classify.py`.
- Classifies requirements into FR, NFR, and BR using Gemini.
- Supports multi-label classifications.

## Current Problems
- Relies on structured fallback rules that assign a default "FR" category with 0.5 confidence if classification fails.
- Does not check confidence thresholds, allowing low-confidence classifications to pass through.
- Lacks support for constraints, assumptions, open questions, and out-of-scope items.

## Production Target
- Perform multi-label classification over `extracted_requirements` with strict thresholding.
- Flag low-confidence classifications for manual review, outputting `classified_requirements`.

## Planned Changes / Enhancements
1. **Separation of Contracts**:
   - **What changes**: Consumes `extracted_requirements` and produces `classified_requirements` (inheriting details with updated `labels` and `classification_confidence`).
   - **Why it changes**: Clean architectural division between discovery (extraction) and verification (classification).
   - **Where it will likely be implemented**: `app/nodes/classify.py`.
   - **Input impact**: `extracted_requirements: List[ExtractedRequirement]`.
   - **Output impact**: `classified_requirements: List[ClassifiedRequirement]`.
   - **Validation impact**: Verified by schema validation checks.
   - **Risk**: downstream mapping breaks. Mitigated by updating all subsequent nodes.
2. **Confidence-Based review Gating**:
   - **What changes**: If classification confidence is below 0.65, set `needs_review = True` and populate `review_reason` explaining the low-confidence classification decision.
   - **Why it changes**: Prevents ambiguous items from entering production environments silently.
   - **Where it will likely be implemented**: Classification post-processing loop.
   - **Input impact**: Requirements list.
   - **Output impact**: Requirements flagged with `needs_review = True`.
   - **Validation impact**: Verified by checking threshold logic in unit tests.
   - **Risk**: Increased review queues.
3. **No FR Default fallback**:
   - **What changes**: If classification fails, propagate the candidate labels with classification confidence set to 0.0 and flag `needs_review = True`, rather than defaulting to `"FR"` category with 0.5 confidence.
   - **Why it changes**: Bypasses the rule violation against generating mock classification assumptions.
   - **Where it will likely be implemented**: Error fallback routines.
   - **Input impact**: Requirements list.
   - **Output impact**: Requirements flagged with zero confidence.
   - **Validation impact**: Checked during classification failure tests.
   - **Risk**: Downstream nodes must check confidence flags.

## Input Contract
- `extracted_requirements`: `List[ExtractedRequirement]`

## Output Contract
- `classified_requirements`: `List[ClassifiedRequirement]`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- If classification LLM fails, copy `extracted_requirements` to `classified_requirements` keeping `candidate_labels` as `labels`, set `classification_confidence = 0.0`, flag `needs_review = True`, and write warnings to state.

## Routing Behavior
- Route to `deduplicate_requirements`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_count` (extracted), `output_count` (classified), `warning_count`, `error_code`, `provider`, `model`.

## Tests Required
- **Unit Test**: Test threshold checks with high and low confidence classifications.
- **Integration Test**: Check that category mappings propagate to the output state.
- **Failure-Path Test**: Verify fallback logic when classification LLM service fails.

## Acceptance Criteria
- [ ] Requirements with confidence below 0.65 are flagged for review.
- [ ] Retains actor, goal, and evidence quotes from the extraction stage.
- [ ] No fake default "FR" category is applied.

## Meeting Notes
- Establish the confidence threshold value (currently proposed as 0.65) to trigger manual review warnings.
