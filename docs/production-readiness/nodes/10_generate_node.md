# Generate Node (`generate_user_stories`)

## Current Status
- Implemented inside `app/nodes/generate.py`.
- Converts classified requirements into Given-When-Then format user stories.
- Returns static fallbacks if the LLM fails.

## Current Problems
- Validation does not check if the output story matches the agile format structure (`As a... I want... so that...`).
- Acceptance criteria are parsed using a simple heuristic (`"Given" in c`) without strict Given-When-Then criteria validation.
- Generates fake story lists if the LLM fails.
- Hardcoded to support only a 1 requirement -> 1 story relationship, failing to support complex mappings.
- Non-story requirements or criteria attachments are incorrectly represented as fake user story objects.

## Production Target
- Generate structured user stories, ensuring Given-When-Then syntax and maintaining requirement category tags.
- Support flexible mapping cardinalities using the `RequirementCoverage` model, producing real `UserStory` objects only for actual stories.

## Planned Changes / Enhancements
1. **Separation of Mapping Logic**:
   - **What changes**: Consumes `classified_requirements` and outputs real `UserStory` objects only when a story is required. For every requirement, populate a `RequirementCoverage` record tracing:
     - `covered_by_story`
     - `split_into_stories`
     - `merged_into_story`
     - `attached_as_acceptance_criteria`
     - `non_story_requirement`
     - `needs_review`
   - **Why it changes**: Clean architecture; non-story requirements (like specific system constraints or out-of-scope declarations) should not generate empty or fake user stories.
   - **Where it will likely be implemented**: `app/nodes/generate.py`.
   - **Input impact**: `classified_requirements: List[ClassifiedRequirement]`.
   - **Output impact**: `user_stories: List[UserStory]` and `requirement_coverages: List[RequirementCoverage]`.
   - **Validation impact**: Checked against coverage rules in unit tests.
   - **Risk**: Complexity in mapping lookups. Mitigated by indexing coverages.
2. **Cardinality Verification**:
   - **What changes**: Support mapping a single user story to multiple source requirement IDs (`source_requirement_ids: List[int]`) and combining their evidence references (`evidence_reference: List[EvidenceSpan]`).
   - **Why it changes**: Enables real-world requirements consolidation (many-to-one) and decomposition (one-to-many).
   - **Where it will likely be implemented**: User story mapper.
   - **Input impact**: State list.
   - **Output impact**: Story models.
   - **Validation impact**: Tracing story associations.
   - **Risk**: ID mismatches. Handled by cross-verifying requirement IDs.
3. **Agile Syntax Checks**:
   - **What changes**: Programmatically validate that every generated user story description contains the keywords: `As a`, `I want`, and `so that` (using regex or string validation). Check that acceptance criteria match the Given-When-Then criteria template.
   - **Why it changes**: Prevents unstructured or invalid stories from propagating.
   - **Where it will likely be implemented**: Story validation function.
   - **Input impact**: Story models.
   - **Output impact**: Validation warnings or gating flags.
   - **Validation impact**: Unit tests verify regex matching.
   - **Risk**: LLM format variation. Mitigated by allowing plain text criteria when appropriate if labeled properly.
4. **No Fake Fallbacks**:
   - **What changes**: If story generation fails, return an empty list and set status to `needs_review`. Never output dummy stories.
   - **Why it changes**: Bypasses the rule violation against generating mock requirements or stories.
   - **Where it will likely be implemented**: Error handling routines.
   - **Input impact**: State list.
   - **Output impact**: Empty stories and coverages set to `needs_review`.

## Input Contract
- `classified_requirements`: `List[ClassifiedRequirement]`

## Output Contract
- `user_stories`: `List[UserStory]`
- `requirement_coverages`: `List[RequirementCoverage]`
- `status`: `str`
- `error`: `Optional[str]`

## Error Behavior
- If LLM calls fail, return an empty list, write warnings/errors in the pipeline state, and set status to `needs_review`. Never output dummy stories.

## Routing Behavior
- Route to `quality_gate`.

## Observability
- Record fields: `job_id`, `trace_id`, `node_name`, `status`, `duration_ms`, `input_count` (requirements), `output_count` (stories generated), `warning_count`, `error_code`, `provider`, `model`.

## Tests Required
- **Unit Test**: Verify regex parsing of generated agile stories and validation of mapping types.
- **Integration Test**: Check multi-cardinality mappings (e.g. many requirements combining into one story).
- **Failure-Path Test**: Verify fallback to `needs_review` when generation service errors out.

## Acceptance Criteria
- [ ] User story description follows agile format.
- [ ] Acceptance criteria contain valid Given-When-Then structures.
- [ ] Requirements metadata (e.g. ID list, category tags) propagates correctly.
- [ ] Stories support many-to-one, one-to-many, and other cardinality types.
- [ ] No fake stories generated for non-story requirements.

## Meeting Notes
- Standardize the target agile user story format for NFRs, Business Rules, and Out-of-Scope items.
