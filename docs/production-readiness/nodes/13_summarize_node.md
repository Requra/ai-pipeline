# Summarize Node (`summarize_structured`)

## Current Status
- Implemented inside `app/nodes/summarize.py`.
- Generates a general executive summary focusing on decisions, open questions, and stakeholder pain points.

## Current Problems
- Output format is unstructured and varies based on the LLM run.
- Missing critical sections (e.g. Scope, Out of Scope, Action Items, Stakeholders, Risks, and Assumptions).
- Using basic mock text if the LLM fails, which is not suitable for production.

## Production Target
- Compile the entire transaction into a structured Pydantic `StructuredSummary` with 9 predictable key sections using actual requirements, stories, coverage records, and grounding links.

## Planned Changes / Enhancements
1. **Pydantic Model Output Integration**:
   - **What changes**: Node output will compile directly into the `StructuredSummary` Pydantic model.
   - **Why it changes**: Standardizes output schemas, easing frontend parsing and rendering.
   - **Where it will likely be implemented**: `app/nodes/summarize.py`.
   - **Input impact**: `raw_text: str`, `classified_requirements: List[ClassifiedRequirement]`, `user_stories: List[UserStory]`, `requirement_coverages: List[RequirementCoverage]`.
   - **Output impact**: `summary: StructuredSummary`.
   - **Validation impact**: Verified by schema validation checks.
   - **Risk**: Strict validation failure on LLM mismatch. Mitigated by structured LLM parsing.
2. **Mandatory 9-Section Scope**:
   - **What changes**: Enforce that the summary details the following 9 sections: Executive Summary, Key Decisions, Open Questions, Risks, Assumptions, Action Items, Stakeholders, Scope, and Out of Scope.
   - **Why it changes**: Guarantees all critical PM analysis headers are present.
   - **Where it will likely be implemented**: Prompt template instructions.
   - **Input impact**: state fields.
   - **Output impact**: Structured summary fields.
   - **Validation impact**: Checked in unit tests.
   - **Risk**: Some sections may be empty. Allowed by returning empty lists for those fields rather than fake text.
3. **No Hallucinated Fallbacks**:
   - **What changes**: If the summarization LLM fails, do not output fake placeholder summaries. Compile a metadata-driven description containing input parameters (e.g. document name, page length, extracted requirement counts) without generating hallucinated text content.
   - **Why it changes**: Production rules strictly prohibit fake fallback generation.
   - **Where it will likely be implemented**: Error handling routines.
   - **Input impact**: State list.
   - **Output impact**: Metadata-only summary.
   - **Validation impact**: Checked during API error tests.
   - **Risk**: Downstream nodes must check for empty/metadata summaries.

## Input Contract
- `raw_text`: `str`
- `classified_requirements`: `List[ClassifiedRequirement]`
- `user_stories`: `List[UserStory]`
- `requirement_coverages`: `List[RequirementCoverage]`

## Output Contract
- `summary`: `StructuredSummary`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- On failure, output a metadata-driven fallback summary based on actual input files and extracted counts. Never invent details or generate fake summaries.

## Routing Behavior
- Route to `export_formatter`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `prompt_tokens`, `completion_tokens`, `estimated_cost`, `provider`, `model`.

## Tests Required
- **Unit Test**: Test presence of required sections in summary outputs.
- **Integration Test**: Verify summary generation with long text files.
- **Failure-Path Test**: Verify fallback to metadata summary when LLM fails.

## Acceptance Criteria
- [ ] Output conforms to `StructuredSummary` Pydantic model.
- [ ] Output contains all 9 required sections.
- [ ] Metadata-driven fallback summary operates correctly if LLM fails.
- [ ] Claims in the summary are grounded in actual requirements.

## Meeting Notes
- Standardize the output format for summaries (Markdown vs. JSON structure) to ease frontend rendering.
