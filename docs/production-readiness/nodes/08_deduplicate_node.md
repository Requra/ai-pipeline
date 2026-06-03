# Deduplicate Node (`deduplicate_requirements`)

## Current Status
- New Node. Currently, naive deduplication is handled in `extract.py` using `(actor, goal)` string matching.

## Current Problems
- Deduplication by `(actor, goal)` can delete distinct requirements (e.g. if the actor is "User" and the goal is "login" but they have different details/behaviors, such as login via OAuth vs login via username/password).
- Deletes grounding citations if duplicates are removed.

## Production Target
- Perform semantic deduplication and merge requirement references, maintaining grounding pointers.

## Planned Changes / Enhancements
1. **Semantic Comparison Logic**:
   - **What changes**: Deduplicate based on text semantic similarity (using embedding thresholds or LLM checks) rather than naive `(actor, goal)` equality.
   - **Why it changes**: Prevents deletion of distinct business constraints that share actors and goals.
   - **Where it will likely be implemented**: `app/nodes/deduplicate.py`.
   - **Input impact**: `classified_requirements: List[ClassifiedRequirement]`.
   - **Output impact**: Consolidates duplicates.
   - **Validation impact**: Checked against overlapping requirements.
   - **Risk**: Semantic overlap false positives. Handled by keeping thresholds conservative (e.g. >0.90 similarity required to merge).
2. **Evidence Citation Preservation**:
   - **What changes**: When two requirements are merged, combine their `evidence: List[EvidenceSpan]` and category `labels: List[RequirementType]`.
   - **Why it changes**: Preserves all backing source citations, ensuring the merged requirement remains fully grounded.
   - **Where it will likely be implemented**: Merge handler function.
   - **Input impact**: State list.
   - **Output impact**: Combined evidence spans.
   - **Validation impact**: Verified by tracing grounding links of merged items.
   - **Risk**: Accumulating too many citations. Mitigated by filtering duplicate spans.
3. **Merge Audit Trail**:
   - **What changes**: Write merge events containing the source requirement IDs and target requirement IDs to the logs and state warnings.
   - **Why it changes**: Provides traceability for auditing system decisions.
   - **Where it will likely be implemented**: Deduplication node.
   - **Input impact**: State list.
   - **Output impact**: Audit warnings appended to `warnings`.
   - **Validation impact**: Verified in integration tests.
   - **Risk**: Warning log bloat.

## Input Contract
- `classified_requirements`: `List[ClassifiedRequirement]`

## Output Contract
- `classified_requirements`: `List[ClassifiedRequirement]` (deduplicated list)
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- On failure, fallback to returning the input list without deduplication and write a warning to the state. Do not crash execution.

## Routing Behavior
- Route to `evidence_grounding`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_count` (pre-dedup), `output_count` (post-dedup), `warning_count` (number of merges), `error_code`.

## Tests Required
- **Unit Test**: Test merging of duplicate requirements with overlapping evidence spans.
- **Integration Test**: Check that requirement IDs remain sequential and correct after deduplication.
- **Failure-Path Test**: Verify fallback to original list when deduplication service errors out.

## Acceptance Criteria
- [ ] No unique requirement details are lost during deduplication.
- [ ] Grounding evidence references from both sources are combined into the consolidated requirement.
- [ ] Conforms to strict category preservation rules.

## Meeting Notes
- Discuss with the team whether semantic deduplication should be rule-based (using embeddings overlap) or LLM-assisted.
