# Node Guide: Extract Node
**Status**: `[UNASSIGNED]`  
**Owner Role**: AI Engineer / NLP Specialist

## 1. Description & Vision
The **Extract Node** takes the clean output from either the Ingest or Transcribe node and pulls out structured data. This is where we identify "Functional Requirements" from raw project text.

**Vision**: A powerful extractor that can distinguish between a user request and a system-level constraint, delivering high-quality, structured output (`List[FunctionalRequirement]`).

## 2. Current Implementation (`extract.py`)
- **Logic**: 
    - Uses Gemini with `with_structured_output` to parse text.
    - Uses `ExtractionResponse` pydantic model to enforce the format.
- **Input**: `state.get("raw_text")`.
- **Output**: `{"functional_requirements": List[FR]}` or `{"error": str}`.

## 3. Expected Enhancements (TODOs)
- [ ] **Complex Parsing**: Improve the prompt to correctly extract `actor` and `goal` for every requirement.
- [ ] **Multi-Document Support**: Handle cases where multiple requirements documents are provided.
- [ ] **Traceability**: Add a `source_hint` to every requirement to map it back to the original text segment.
- [ ] **Validation**: Ensure that every requirement has at least a description and an ID.

## 4. Operational Guidelines
- **Constraint Handling**: Don't extract non-functional requirements (performance, security) as functional ones.
- **Atomic Requirements**: Every requirement should be one action (e.g., "The user shall log in").
- **Deduplication**: If a requirement is repeated in the raw text, only extract it once.

## 5. Verification Checklist
- [ ] Are all requirements assigned a unique numeric ID?
- [ ] Is there an actor (e.g., "User", "System") for every requirement?
- [ ] Is the list empty if no requirements are found?
- [ ] Does it handle multi-paragraph text without missing details?
