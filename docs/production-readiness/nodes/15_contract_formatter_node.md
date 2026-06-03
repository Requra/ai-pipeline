# Contract Formatter Node (`contract_formatter`)

## Current Status
- New Node. Currently, the format node (`format.py`) only determines a status string. The API endpoint directly returns the internal pipeline state dictionary (minus `raw_bytes`).

## Current Problems
- Exposes internal state variables (e.g. `raw_text`, `is_useful`, `relevance_score`, internal routing indicators) to clients.
- Unstable API contracts: changes to internal structures break frontend integrations.

## Production Target
- Map pipeline state to a strict, production-ready `JobResult` schema. This is the final node in the graph and the only node allowed to assemble the client response.

## Planned Changes / Enhancements
1. **Isolated response Assembly**:
   - **What changes**: The node will strictly map the state variables into a Pydantic `JobResult` instance, discarding all internal graph variables.
   - **Why it changes**: Prevents internal changes from leaking to external clients.
   - **Where it will likely be implemented**: `app/nodes/contract_formatter.py`.
   - **Input impact**: state fields.
   - **Output impact**: `JobResult` JSON payload.
   - **Validation impact**: Checked against Pydantic schema validation.
   - **Risk**: API serialization crashes if type checks fail. Mitigated by error safety gates.
2. **Pydantic Validation Guard**:
   - **What changes**: If the compiled response fails `JobResult` validation, intercept the error and return a safe error payload conforming to the schema.
   - **Why it changes**: Guarantees that clients always receive a predictable error schema instead of a generic HTTP 500 error page.
   - **Where it will likely be implemented**: `app/nodes/contract_formatter.py`.
   - **Input impact**: state fields.
   - **Output impact**: Structured error payload.
   - **Validation impact**: Tested by passing malformed fields.
   - **Risk**: Hides developer serialization bugs. Mitigated by logging detailed errors.
3. **Trace and Timing Compilation**:
   - **What changes**: Calculate total processing duration using `started_at` and compile the list of warning and error events.
   - **Why it changes**: Provides standard metadata for debugging.
   - **Where it will likely be implemented**: `app/nodes/contract_formatter.py`.
   - **Input impact**: `started_at: float`.
   - **Output impact**: `processing_time_ms` and lists of warnings.
   - **Validation impact**: Checked in integration tests.
   - **Risk**: Timing variance due to server load.

## Input Contract
- `job_id`: str
- `status`: str
- `is_useful`: bool
- `relevance_score`: float
- `user_stories`: List[UserStory]
- `classified_requirements`: List[ClassifiedRequirement]
- `requirement_coverages`: List[RequirementCoverage]
- `summary`: Optional[StructuredSummary]
- `export_rows`: List[ExportRow]
- `quality_issues`: List[QualityIssue]
- `warnings`: List[PipelineWarning]
- `error`: Optional[str]
- `started_at`: float

## Output Contract
- Conforms strictly to the `JobResult` Pydantic model (returned directly by API endpoints).

## Error Behavior
- On validation failures, construct a default error `JobResult` payload containing the job ID and error warnings to avoid serialization errors. Never return raw internal states.

## Routing Behavior
- Route directly to `END`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `warning_count`, `error_code`.

## Tests Required
- **Unit Test**: Test serialization against the `JobResult` Pydantic schema.
- **Contract Test**: Verify response format contains all required client properties.
- **Failure-Path Test**: Verify safe error payload structure when validation fails.

## Acceptance Criteria
- [ ] Output response aligns with the `JobResult` Pydantic model.
- [ ] Internal graph states are not exposed to external clients.
- [ ] Returns structured error schema if processing fails.

## Meeting Notes
- Review response model constraints with the frontend team before merging contract changes.
