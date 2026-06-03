# Quality Gate Node (`quality_gate`)

## Current Status
- New Node. No current validation of requirements or user stories.

## Current Problems
- Weak acceptance criteria, duplicate stories, invalid labels, missing actors, or ungrounded requirements are passed through to the database without gate validations.

## Production Target
- Inspect stories, requirements, and coverage structures. Flag violations and determine if routing to the repair node is needed.

## Planned Changes / Enhancements
1. **Coverage Verification**:
   - **What changes**: Ensure that every requirement in `classified_requirements` has a corresponding `RequirementCoverage` record.
   - **Why it changes**: Prevents requirements from being dropped or ignored during story mapping.
   - **Where it will likely be implemented**: `app/nodes/quality_gate.py`.
   - **Input impact**: `classified_requirements`, `requirement_coverages`.
   - **Output impact**: `quality_issues` populated on failure.
   - **Validation impact**: Checked in integration tests.
   - **Risk**: Minor formatting issues might trigger coverage gaps.
2. **Story Mappings Validation**:
   - **What changes**: Verify that every `UserStory` contains a non-empty `source_requirement_ids` list and a valid `evidence_reference` matching chunk quotes.
   - **Why it changes**: Guarantees traceability of every story.
   - **Where it will likely be implemented**: `app/nodes/quality_gate.py`.
   - **Input impact**: `user_stories`.
   - **Output impact**: `quality_issues` populated.
   - **Validation impact**: Verified by testing stories with empty references.
   - **Risk**: Deletion of unmapped stories.
3. **Acceptance Criteria Testability Check**:
   - **What changes**: Programmatically analyze acceptance criteria to verify they are not empty and conform to Given-When-Then criteria rules or simple non-empty strings.
   - **Why it changes**: Prevents low-quality placeholder text (like "works as expected") from passing through.
   - **Where it will likely be implemented**: `app/nodes/quality_gate.py`.
   - **Input impact**: `user_stories`.
   - **Output impact**: `quality_issues` populated.
   - **Validation impact**: Unit tests verify testability checkers.
   - **Risk**: Rejection of short but valid criteria. Managed by tuning the criteria checker.
4. **Severity Gating and Routing**:
   - **What changes**: Categorize issues as `low`, `medium`, or `high` severity. If any `medium` or `high` severity issues are detected, route to `repair_if_needed` (unless the repair loop limit has been reached).
   - **Why it changes**: Allows automatic self-correction for critical issues while logging warnings for minor ones.
   - **Where it will likely be implemented**: `app/nodes/quality_gate.py`.
   - **Input impact**: `quality_issues`.
   - **Output impact**: Routing command.
   - **Validation impact**: Verified in loop tests.
   - **Risk**: Infinite loops. Mitigated by checking the repair loop counter in the state.

## Input Contract
- `classified_requirements`: `List[ClassifiedRequirement]`
- `user_stories`: `List[UserStory]`
- `requirement_coverages`: `List[RequirementCoverage]`
- `quality_issues`: `List[QualityIssue]`
- `warnings`: `List[PipelineWarning]`

## Output Contract
- `quality_issues`: `List[QualityIssue]`
- `status`: `str` (determines routing)

## Error Behavior
- If the quality gate checks throw exceptions, halt and log critical failures, write warnings, set status to `needs_review`, and route directly to summary.

## Routing Behavior
- **Pass 1 Routing**: If `quality_issues` contains any `medium` or `high` severity issues and the repair loop counter in the state is less than 2, route to `repair_if_needed`. Otherwise, route to `summarize_structured`.
- **Pass 2 Routing** (After Repair): If quality issues persist, do not route back to repair; mark unresolved items as `needs_review = True`, write warnings, and route to `summarize_structured`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_count` (items checked), `output_count` (issues found), `warning_count`, `error_code`.

## Tests Required
- **Unit Test**: Test validation rules with malformed requirements and stories.
- **Integration Test**: Check graph routing transitions when issues are resolved or persist.
- **Failure-Path Test**: Verify execution bypasses repair loops when state counters are corrupted.

## Acceptance Criteria
- [ ] Stories missing actors or goals are caught and recorded.
- [ ] Requirements lacking grounding references are flagged.
- [ ] Every requirement must have a corresponding `RequirementCoverage` record.
- [ ] Every story must map to source requirement IDs.

## Meeting Notes
- Establish severity ratings (low, medium, high) for quality issues and align on which severity levels should trigger the repair loop.
