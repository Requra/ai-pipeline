
````md
You are continuing Requra/ai-pipeline on branch review/full-pipeline-merge.

Current confirmed bug:
LangGraph Studio fails with:

Invalid state update, expected dict with one or more of PipelineState keys...
got {..., "error_message": "...", "status": "partial"}

Also generate_node logs:

1 validation error for GenerationResponse
stories
  Field required

Root cause:
1. `GenerationResponse` expects key `stories`.
2. OpenRouter/model sometimes returns key `user_stories`.
3. generate_node parser only supports:
   - direct list
   - {"stories": [...]}
4. Then parsing fails.
5. The fallback succeeds and creates user_stories + requirement_coverages.
6. But fallback return includes `error_message`, which is NOT part of PipelineState.
7. LangGraph Studio rejects the state update.

Do NOT:
- Add new graph nodes.
- Change graph order.
- Remove fallback story generation.
- Fake requirements.
- Remove Pydantic validation.
- Break FastAPI `/process-json`.

Required fixes:

1. Fix generate parser normalization

In `app/nodes/generate.py`, update parsing logic.

Current behavior supports:
- list → {"stories": list}
- {"stories": [...]}

Add support for:
- {"user_stories": [...]}
- {"items": [...]}
- {"data": [...]}

Suggested helper:

```python
def normalize_generation_payload(parsed: Any) -> dict:
    if isinstance(parsed, list):
        return {"stories": parsed}

    if not isinstance(parsed, dict):
        raise ValueError(f"Generation output must be dict or list, got {type(parsed).__name__}")

    if "stories" in parsed:
        return parsed

    if "user_stories" in parsed:
        return {"stories": parsed["user_stories"]}

    if "items" in parsed:
        return {"stories": parsed["items"]}

    if "data" in parsed:
        return {"stories": parsed["data"]}

    return parsed
````

Then:

```python
parsed = json.loads(content)
normalized = normalize_generation_payload(parsed)
response = GenerationResponse.model_validate(normalized)
```

2. Make generation prompt stricter

Update prompt to clearly say:

Return JSON exactly in this shape:

```json
{
  "stories": [
    {
      "id": 1,
      "title": "Register account",
      "description": "As a user, I want to register using email and password, so that I can access the CRM.",
      "acceptance_criteria": [
        "Given a new user, when they submit valid email and password, then the account is created."
      ],
      "labels": ["FR"]
    }
  ]
}
```

Do NOT return:

* `user_stories`
* markdown
* explanation
* plain text

But keep parser normalization because LLMs may still return `user_stories`.

3. Remove invalid `error_message` from generate_node return

In `generate_node` fallback return, replace:

```python
return {
    **result,
    "error_message": str(e),
    "status": "partial"
}
```

with:

```python
existing_warnings = state.get("warnings", []) or []
new_warnings = [
    {
        "node_name": "generate",
        "code": "GENERATE_LLM_PARSE_FALLBACK",
        "message": f"Generation LLM output could not be parsed; fallback stories were generated. Error: {type(e).__name__}: {str(e)}"
    }
]

return {
    **result,
    "warnings": existing_warnings + new_warnings,
    "status": "partial"
}
```

Important:
Do not return `error_message` from any node except inside final `JobResult` construction in `format_node`.

PipelineState has `error`, not `error_message`.

4. Search entire codebase for invalid state key

Run search:

```bash
grep -R "error_message" -n app
```

Rules:

* Node return dicts must not return `error_message`.
* Internal final schema `JobResult.error_message` is allowed only inside format/result serialization.
* Graph state should use `error`.

If a node wants to report non-fatal issue:

* use `warnings`

If fatal:

* use `error`

5. Improve fallback story quality

Current fallback produces bad text like:

```txt
As a None, I want None, so that...
```

Fix fallback:

If `req.actor` is missing:

* use `"system"` for NFR/BR
* use `"user"` for generic FR
* use `"admin"` if text contains admin/admins
* use `"sales representative"` if text contains sales representative
* use `"viewer"` if text contains viewer

If `req.goal` is missing:

* derive short goal from requirement text
* or use `"satisfy this requirement"`

Never produce:

* `As a None`
* `I want None`

6. Do not generate user stories for Open Questions and Out-of-Scope

If labels include only:

* Open Question
* Out-of-Scope
* Assumption

then do not create user stories.

Instead create coverage:

```python
coverage_type = "non_story"
reason = "Open questions/out-of-scope/assumptions are not converted into user stories."
```

For BR/NFR:

* It is acceptable to generate a story if mapped to implementation behavior.
* But pure business rules can also be `attached` or `non_story` later.
* For MVP, generating stories for FR/NFR/BR is acceptable, but not for Open Question and Out-of-Scope.

7. Add tests

Add tests for generate normalization:

Test 1:
Input:

```json
{"user_stories": [{"id": 1, "title": "...", "description": "...", "acceptance_criteria": ["..."], "labels": ["FR"]}]}
```

Expected:

* validates as GenerationResponse
* output has user_stories

Test 2:
Input:

```json
{"stories": [...]}
```

Expected pass.

Test 3:
Direct list input passes.

Test 4:
Fallback return does not contain `error_message`.

Test 5:
Fallback does not produce `As a None`.

Test 6:
Open Question / Out-of-Scope requirements do not create user stories; they create non_story coverage.

8. Validate

Run:

```bash
cd ai-service
poetry run pytest -q
```

Then restart API:

```bash
poetry run uvicorn app.main:app --reload --log-level debug
```

Postman test:

```txt
POST http://127.0.0.1:8000/process-json
```

Expected:

* requirements not empty
* user_stories not empty
* requirement_coverages not empty
* no Invalid state update
* no `error_message` key returned from intermediate node
* final response may include `error_message` only as part of JobResult, and should be null if no fatal error

Then LangGraph Studio:

```bash
poetry run langgraph dev
```

Expected:

* Studio run does not fail with Invalid state update
* graph reaches format node
* final state includes job_result

```

## Current status

You are very close now.

The pipeline already extracted enough to produce **29 fallback stories** and coverage records. The remaining blocker is mostly **state contract cleanup** and **generation output normalization**.
```
