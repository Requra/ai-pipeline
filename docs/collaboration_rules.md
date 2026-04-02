# AI Pipeline: Team Collaboration & Best Practices
## 1. Core Principles
To maximize production and ensure a high-quality product, we follow these core principles:
- **Clean Slate**: Every node starts with a defined input from the `state` and returns only what is needed.
- **Fail Gracefully**: If your node's primary task fails, return an error message but provide what data you *did* manage to process.
- **Async First**: All node functions are `async` to allow for non-blocking I/O (LLM calls, file reads).

## 2. State Management (`PipelineState`)
The `state` is a shared dictionary that travels through the pipeline.
- **Rule**: Never delete data from the `state`. Only add or update keys.
- **Naming**: Use snake_case for all keys (e.g., `raw_text`, `functional_requirements`).
- **Data Integrity**: If you are modifying an existing list, append to it rather than overwriting it, unless the logic explicitly requires broad changes.

## 3. Error Handling Protocol
Every node must handle exceptions and return an `error` key in the result dictionary.
- **Standard Format**: `NODE_CODE: error message`.
- **Examples**:
    - `INGEST_EMPTY: no text extracted`
    - `TRANSCRIBE_LLM_FAILURE: Gemini is down`
- **Impact**: Errors do not stop the pipeline but are gathered by the **Format Node** to inform the final status.

## 4. Pydantic & Type Safety
We use Pydantic models (located in `app.schemas.items`) for all structured data.
- **Requirement**: Any complex object returned by a node (Requirements, Stories, Classifications) **must** be a Pydantic model.
- **Benefit**: This ensures that downstream nodes can rely on the data structure without complex nested dictionary checks.

## 5. Team Workflow & Scrum
As discussed, node assignments will be finalized during our **Scrum Meeting**.
- **Ownership**: Once assigned, you are the technical lead for that node.
- **Daily Standup**: Brief report on "Completed", "Pending", and "Blockers".
- **Code Reviews**: Every change must be reviewed by at least one other team member.
- **PR Labels**: Use labels like `node-ingest` or `bugfix` to help me track progress.

---
*Let's build a robust, scalable AI Pipeline together!*
