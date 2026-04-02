# Node Guide: Generate Node
**Status**: `[UNASSIGNED]`  
**Owner Role**: AI Engineer / Content Specialist

## 1. Description & Vision
The **Generate Node** is responsible for creating a set of "User Stories" from the original requirements. This node bridges the gap between raw text and actionable engineering tasks.

**Vision**: A high-fidelity generator that produces clear, Jira-ready user stories that include AC (Acceptance Criteria) and "As a... I want to... So that..." format.

## 2. Current Implementation (`generate.py`)
- **Logic**: 
    - Uses Gemini with `with_structured_output` for generating User Stories.
    - Uses `UserStoryResponse` pydantic model to enforce the format.
- **Input**: `state.get("raw_text")`.
- **Output**: `{"user_stories": List[UserStory]}` or `{"error": str}`.

## 3. Expected Enhancements (TODOs)
- [ ] **Acceptance Criteria**: Add a field for `acceptance_criteria` to every User Story.
- [ ] **Sprint Points**: Suggest a rough estimation of points (1, 2, 3, 5, 8) based on story complexity.
- [ ] **Prioritization**: Add a "Priority" field to each story (High, Medium, Low).
- [ ] **Linking**: Automatically link stories to their related `functional_requirements`.

## 4. Operational Guidelines
- **Granularity**: Keep stories small enough to be completed in a single sprint.
- **Consistency**: Ensure all stories follow the standard "As a... I want... So that..." format.
- **Independence**: Each story should be as independent as possible (INVEST principle).

## 5. Verification Checklist
- [ ] Do all generated stories have a title, actor, and goal?
- [ ] Are they mapped to a unique ID?
- [ ] Does it handle cases where `raw_text` is missing or short?
- [ ] Is there at least one user story per requirement?
