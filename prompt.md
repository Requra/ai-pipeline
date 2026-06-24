
````md
You are a senior Python/FastAPI/LangGraph engineer working on Requra AI Pipeline.

Repository:
https://github.com/Requra/ai-pipeline

Current branch:
feat/contract

Task:
Move all inline LLM system prompts from node files into shared, versioned prompt files, then load them through a safe prompt registry/loader.

Important:
Do NOT change the public response contract.
Do NOT change the pipeline behavior unless required by moving prompts.
Do NOT rewrite unrelated code.
Do NOT modify model/provider logic unless necessary.
Do NOT change output schemas.
Keep changes focused, clean, and easy to review.

Current problem:
System prompts are currently embedded directly inside node files such as ingest, extract, classify, generate, and summarize. This makes prompt behavior hard to review, easy to accidentally change, and unsafe for MVP handoff.

Goal:
Prompts should become stable product assets, not hidden strings inside business logic.

Implement this structure:

ai-service/app/prompts/
  __init__.py
  loader.py
  registry.py
  templates/
    ingest_relevance_v1.md
    extract_requirements_v1.md
    classify_requirements_v1.md
    generate_user_stories_v1.md
    summarize_structured_v1.md

Implementation requirements:

1. Create prompt template files
- Move each existing inline system prompt into its own `.md` file.
- Preserve the current prompt text as much as possible.
- Do not improve or rewrite prompt quality in this task unless there is a clear bug.
- Use `_v1` naming to make prompt versions explicit.

2. Create a prompt registry
Create:

ai-service/app/prompts/registry.py

It should define stable prompt IDs, for example:

- INGEST_RELEVANCE_V1
- EXTRACT_REQUIREMENTS_V1
- CLASSIFY_REQUIREMENTS_V1
- GENERATE_USER_STORIES_V1
- SUMMARIZE_STRUCTURED_V1

Map each prompt ID to its template file path.

3. Create a prompt loader
Create:

ai-service/app/prompts/loader.py

The loader should:
- Load prompt files from `ai-service/app/prompts/templates`
- Use UTF-8
- Cache loaded prompts to avoid repeated disk reads
- Raise a clear error if a prompt ID or file is missing
- Keep the API simple, for example:

```py
from app.prompts.registry import PromptId
from app.prompts.loader import load_prompt

prompt = load_prompt(PromptId.EXTRACT_REQUIREMENTS_V1)
````

4. Refactor node files
   Update nodes that currently contain inline prompt strings.

Likely files to inspect:

* ai-service/app/nodes/ingest.py
* ai-service/app/nodes/extract.py
* ai-service/app/nodes/classify.py
* ai-service/app/nodes/generate.py
* ai-service/app/nodes/summarize.py

Replace inline prompt strings with calls to the prompt loader.

Example:

```py
system_prompt = load_prompt(PromptId.EXTRACT_REQUIREMENTS_V1)
```

If a node combines system prompt + dynamic user content, keep only the static system prompt in the `.md` file and keep runtime content built in Python.

5. Add prompt tests
   Create tests:

tests/prompts/test_prompt_registry.py
tests/prompts/test_prompt_loader.py

Tests should verify:

* Every registered prompt exists.
* Every registered prompt loads successfully.
* No registered prompt is empty.
* Loader raises a clear exception for missing prompt IDs/files.
* Prompt files are UTF-8 readable.

6. Add prompt snapshot protection
   Create:

tests/prompts/test_prompt_snapshots.py

Purpose:
Detect accidental prompt edits.

Approach:

* Store expected hashes for each prompt file in a simple dictionary inside the test.
* Compute SHA256 for each prompt file.
* If a prompt changes, the test should fail with a clear message saying:
  “Prompt changed intentionally? Update snapshot hash after reviewing output quality.”

Important:
This is not security protection. It is review protection.

7. Keep imports clean
   Avoid circular imports.
   Do not import nodes inside prompt loader.
   Do not load prompts at module import time if that makes tests or startup fragile.
   Prefer simple lazy loading.

8. Update docs
   Add a short document:

docs/prompts/prompt-management.md

Include:

* Where prompts live
* How to add a new prompt
* How to version prompts
* How to update snapshot hashes
* Rule: prompt changes require PR review and contract/golden tests

9. Run tests
   Run:

```bash
cd ai-service
poetry run pytest
```

If the repo uses another test command, inspect pyproject.toml and use the correct one.

10. Final report
    After implementation, provide a report with:

* Files created
* Files modified
* Prompts moved
* Tests added
* Test results
* Any behavior changes, if any
* Any prompts that were not moved and why

Acceptance criteria:

* No inline large system prompts remain in node files.
* Prompt files are centralized under `ai-service/app/prompts/templates`.
* Nodes load prompts through registry/loader.
* Existing contract tests still pass.
* Existing mocked E2E tests still pass.
* New prompt loader/registry/snapshot tests pass.
* Public response contract is unchanged.
* Changes are small and reviewable.

````

Add this extra line if you want him to be stricter:

```md
Before editing, first search the whole repo for large inline prompt strings, SYSTEM_PROMPT, prompt = """, and LLM instruction blocks. Create a checklist, then move only the confirmed static system prompts.
````
