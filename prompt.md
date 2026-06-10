Refactor generate_node to make requirement-to-story mapping deterministic and production-safe.

Current issue:
generate_node relies on LLM story id matching requirement id:
- source_req = req_map.get(s.id)
- source_requirement_ids=[s.id]

This is fragile because the LLM may renumber, skip, duplicate, reorder, or treat id as story id.

Required changes:
1. Change StoryResponse schema:
   - replace id: int with source_requirement_id: int
2. Prompt the LLM to return source_requirement_id exactly matching the input requirement id.
3. Generate internal story IDs in code:
   story_id = f"{job_id}_story_{source_requirement_id}"
4. Validate all returned source_requirement_id values:
   - must exist in req_map
   - no duplicates
   - no missing actionable requirements unless explicitly marked needs_review
5. Use labels + candidate_labels for non-story filtering:
   if any label in {"Open Question", "Out-of-Scope", "Assumption"} then skip story generation and create non_story coverage.
6. Remove silent default ["FR"] from _normalize_labels.
7. Fill evidence_reference from source requirement in code.
8. Fill acceptance criterion IDs in code.
9. Create requirement_coverages in code, not from LLM.
10. Add tests for:
   - LLM renumbers ids
   - LLM duplicates source_requirement_id
   - LLM skips a requirement
   - non-story requirement is never generated
   - evidence and AC IDs are always filled.

Do not add RAG in this refactor.
RAG will be added later only for examples/glossary/context, not ID mapping.