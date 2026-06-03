# Evidence Grounding Node (`evidence_grounding`)

## Current Status
- New Node. Currently, there is no verification that requirements exist in the source document.

## Current Problems
- The LLM can generate requirements not supported by the source text, leading to hallucination.

## Production Target
- Verify that every requirement and user story is grounded in original source text, rejecting or flagging any items with missing quotes.

## Planned Changes / Enhancements
1. **Verification Logic**:
   - **What changes**: The node will programmatically check that each requirement has `len(evidence) >= 1` and that every `EvidenceSpan.quote` matches the corresponding `SourceChunk.text` using character substring check or character overlap.
   - **Why it changes**: Prevents hallucinated items from bypassing quality controls.
   - **Where it will likely be implemented**: `app/nodes/evidence_grounding.py`.
   - **Input impact**: `classified_requirements: List[ClassifiedRequirement]`, `chunks: List[SourceChunk]`.
   - **Output impact**: `classified_requirements` updated with flags, `quality_issues` populated on failures.
   - **Validation impact**: Verified by testing ungrounded requirements.
   - **Risk**: Small punctuation discrepancies could flag false positives. Mitigated by using a sliding-window character matching threshold (e.g., Levenshtein distance or normalized whitespace comparison).
2. **LLM Validation Fallback (Secondary Only)**:
   - **What changes**: Only use LLM checks if the string match is near the margin (e.g. paraphrase or translation boundary), but never rely on it as the primary grounding pass.
   - **Why it changes**: Programmatic checks are deterministic and cheaper; LLM is a secondary validator.
   - **Where it will likely be implemented**: `app/nodes/evidence_grounding.py`.
   - **Input impact**: State list.
   - **Output impact**: State list.
   - **Validation impact**: Checked in integration tests.
   - **Risk**: Increased latency.
3. **Empty Evidence Gating**:
   - **What changes**: If a requirement has an empty `evidence` list or the quote is completely missing from source chunks, set `needs_review = True`, populate `review_reason = "Missing source quote grounding"`, and log a warning.
   - **Why it changes**: Mandates grounding transparency.
   - **Where it will likely be implemented**: Grounding loop.
   - **Input impact**: State list.
   - **Output impact**: Updated requirements.
   - **Validation impact**: Verified in unit tests.
   - **Risk**: Blocks automated flows for marginally grounded items.

## Input Contract
- `classified_requirements`: `List[ClassifiedRequirement]`
- `chunks`: `List[SourceChunk]`

## Output Contract
- `classified_requirements`: `List[ClassifiedRequirement]` (grounded, with updated flags)
- `quality_issues`: `List[QualityIssue]`
- `status`: `str`

## Error Behavior
- If grounding checks fail or throw exceptions, default to setting `needs_review = True` on all unverified requirements. Never allow ungrounded active requirements to bypass verification.

## Routing Behavior
- Route to `generate_user_stories`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_count` (requirements), `output_count` (grounded), `warning_count`, `error_code`.

## Tests Required
- **Unit Test**: Test verification logic with grounded and hallucinated requirement samples.
- **Integration Test**: Check grounding validation when processing long documents.
- **Failure-Path Test**: Verify fallback behaviors if string distance libraries throw exceptions.

## Acceptance Criteria
- [ ] Requirements missing matching source quotes are flagged.
- [ ] Populates descriptive `review_reason` fields for ungrounded requirements.
- [ ] Ensures `len(evidence) >= 1` is strictly checked.

## Meeting Notes
- Determine the threshold of grounding failure (e.g. percentage of ungrounded requirements) that should block the pipeline and trigger alerts.
