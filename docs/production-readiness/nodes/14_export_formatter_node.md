# Export Formatter Node (`export_formatter`)

## Current Status
- New Node. Currently, there is no export functionality.

## Current Problems
- The frontend must parse JSON to build CSV/Jira exports, creating duplication of logic.
- No backend-supported mechanism to represent complex requirement-to-story mappings in flat tables.

## Production Target
- Standardize export formats (Jira-ready, Excel, CSV) directly on the backend, running *before* the contract formatter node.

## Planned Changes / Enhancements
1. **Pre-Response Integration**:
   - **What changes**: The export formatter node is executed before `contract_formatter` so that the computed `export_rows` list is populated directly inside the `JobResult` API response.
   - **Why it changes**: Clean single-endpoint API integration, providing export-ready rows in the same transaction.
   - **Where it will likely be implemented**: `app/graph/pipeline.py` execution edges.
   - **Input impact**: `classified_requirements`, `user_stories`, `requirement_coverages`.
   - **Output impact**: `export_rows: List[ExportRow]`.
   - **Validation impact**: Checked against `JobResult` validation.
   - **Risk**: Marginal payload size increase. Handled by keep fields compact.
2. **Tabular Mappings**:
   - **What changes**: Map requirements without stories (e.g. non-story, out-of-scope, or constraints) as separate rows. Map stories mapped to multiple requirements by aggregating titles and ids into flat string lists.
   - **Why it changes**: Standardizes output lists for spreadsheet rendering.
   - **Where it will likely be implemented**: `app/nodes/export_formatter.py`.
   - **Input impact**: State lists.
   - **Output impact**: Tabular models.
   - **Validation impact**: Verified by checking row counts.
   - **Risk**: ID mapping bugs.
3. **Safe CSV Escaping**:
   - **What changes**: Programmatically escape double-quotes, commas, and formatting boundaries in text quotes and criteria fields to prevent CSV injection or column alignment shifting.
   - **Why it changes**: Ensures export files are secure and open correctly in spreadsheet tools.
   - **Where it will likely be implemented**: CSV string formatter utility.
   - **Input impact**: state fields.
   - **Output impact**: Escaped text fields.
   - **Validation impact**: Tested with inputs containing quotes and commas.
   - **Risk**: Minor format degradation in raw CSV.

## Input Contract
- `classified_requirements`: `List[ClassifiedRequirement]`
- `user_stories`: `List[UserStory]`
- `requirement_coverages`: `List[RequirementCoverage]`

## Output Contract
- `export_rows`: `List[ExportRow]`
- `status`: `str`

## Error Behavior
- On failure, populate `export_rows` with an empty list, write warnings to state, and continue execution. Never crash the pipeline.

## Routing Behavior
- Route to `contract_formatter`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_count` (items mapped), `output_count` (rows produced), `warning_count`, `error_code`.

## Tests Required
- **Unit Test**: Verify mapping logic from user stories to CSV row formats.
- **Integration Test**: Check fields contain no unescaped commas or bad characters that disrupt CSV parsers.
- **Failure-Path Test**: Verify execution continues with empty rows if schemas are corrupted.

## Acceptance Criteria
- [ ] Requirements and user stories map to standard tabular rows.
- [ ] Export formats contain Jira-ready fields.
- [ ] Safely escapes double-quotes and formatting boundaries.
- [ ] Runs before the contract formatter.

## Meeting Notes
- Align on the default CSV column header layout to ensure integration with Jira import workflows.
