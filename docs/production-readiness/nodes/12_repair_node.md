# Repair Node (`repair_if_needed`)

## Current Status
- New Node. There is currently no mechanism to self-correct LLM or parsing errors.

## Current Problems
- Minor formatting mistakes or schema variations result in pipeline failure or manual review alerts.

## Production Target
- Automatically correct quality gate violations using self-correction prompts, limiting repair loops to a maximum of 2 attempts.

## Planned Changes / Enhancements
1. **State-Tracked Loop Gating**:
   - **What changes**: Implement a `repair_attempts: int` loop counter in the graph state. The repair node increments this counter on each entry.
   - **Why it changes**: Prevents infinite routing loops between the quality gate and the repair node.
   - **Where it will likely be implemented**: `app/nodes/repair.py` and graph transition logic.
   - **Input impact**: `repair_attempts: int`.
   - **Output impact**: `repair_attempts` incremented.
   - **Validation impact**: Verified by testing loop termination.
   - **Risk**: Overrun of API timeout limits if LLM is slow.
2. **Grounding-Constrained Prompts**:
   - **What changes**: The repair prompt will strictly instruct the LLM to correct only structural issues (e.g. rewrite story in As-I-So format, format acceptance criteria). It is strictly forbidden from inventing missing evidence or generating unsupported features.
   - **Why it changes**: Adheres to strict anti-hallucination policies.
   - **Where it will likely be implemented**: `app/nodes/repair.py` prompt template.
   - **Input impact**: state lists.
   - **Output impact**: Corrected requirement/story listings.
   - **Validation impact**: Unit tests check repaired items are grounded in the original source.
   - **Risk**: Repaired items may still fail the quality gate if the LLM cannot resolve the issue. Handled by gating.
3. **Unresolved Items Gating**:
   - **What changes**: If the loop counter reaches 2 and the quality gate still detects failures, set `needs_review = True` and write the details to `review_reason` for the unresolved items.
   - **Why it changes**: Ensures honest propagation of issues rather than generating silent failures or mock overrides.
   - **Where it will likely be implemented**: `app/nodes/repair.py` fallback path.
   - **Input impact**: state lists.
   - **Output impact**: State items flagged.
   - **Validation impact**: Verified in integration tests.
   - **Risk**: Increases the proportion of items in review queue.

## Input Contract
- `quality_issues`: `List[QualityIssue]`
- `classified_requirements`: `List[ClassifiedRequirement]`
- `user_stories`: `List[UserStory]`
- `repair_attempts`: `int` (default 0)

## Output Contract
- `classified_requirements`: `List[ClassifiedRequirement]`
- `user_stories`: `List[UserStory]`
- `repair_attempts`: `int`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- On failure, stop repair attempts, set state status to `needs_review`, write logs/warnings, and route directly to summary. Never generate mock replacements.

## Routing Behavior
- Increment the loop counter and route directly back to `quality_gate`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `attempt_count` (current loop count), `resolved_issue_count`, `remaining_issue_count`, `error_code`.

## Tests Required
- **Unit Test**: Verify repair prompts resolve simple format violations.
- **Integration Test**: Check loop counter limits execution to 2 attempts.
- **Failure-Path Test**: Verify execution bypasses repair loops when model is unavailable.

## Acceptance Criteria
- [ ] Resolves formatting issues automatically without altering document context.
- [ ] Terminates execution loop if errors are not resolved within 2 attempts.
- [ ] Never invents details or creates ungrounded requirement/story mappings.

## Meeting Notes
- Review self-correction prompts to ensure LLMs do not invent details when repairing missing requirements context.
