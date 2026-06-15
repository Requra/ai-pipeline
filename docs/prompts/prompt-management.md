# Prompt Management

All LLM system prompts for the Requra AI Pipeline are centralized and versioned to ensure stability, safety, and easy review.

## Directory Structure

- `ai-service/app/prompts/templates/`: Stores prompt templates as `.md` files.
- `ai-service/app/prompts/registry.py`: Defines `PromptId` and maps IDs to template files.
- `ai-service/app/prompts/loader.py`: Handles loading and caching of prompts.

## How to Add a New Prompt

1. Create a new `.md` file in `app/prompts/templates/`. Use a descriptive name with a version suffix (e.g., `my_new_node_v1.md`).
2. Add a new entry to the `PromptId` enum in `app/prompts/registry.py`.
3. Add the mapping to `PROMPT_MAP` in `app/prompts/registry.py`.
4. Use the prompt in your node:
   ```python
   from app.prompts.loader import load_prompt
   from app.prompts.registry import PromptId

   prompt = load_prompt(PromptId.MY_NEW_NODE_V1)
   ```

## How to Version Prompts

When changing a prompt's behavior significantly, create a new version:
1. Create `..._v2.md`.
2. Add `..._V2` to `PromptId` and `PROMPT_MAP`.
3. Update the node to use the new version.

## Snapshot Protection

We use SHA256 snapshots to detect accidental prompt changes.
- Test file: `ai-service/tests/prompts/test_prompt_snapshots.py`
- If you intentionally change a prompt, the test will fail.
- **Action:** Review the prompt change quality, then update the expected hash in the test file.

## Rules

- **PR Review Required:** Any change to a `.md` template file or the snapshot test MUST be reviewed.
- **UTF-8:** All prompt files must use UTF-8 encoding.
- **No Inline Prompts:** Avoid large system prompts directly in Python code. Small, dynamic user instructions can remain in Python if they change per-request.
