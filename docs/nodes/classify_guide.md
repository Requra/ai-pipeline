# Node Guide: Classify Node
**Status**: `[UNASSIGNED]`  
**Owner Role**: AI Analyst / Backend Developer

## 1. Description & Vision
The **Classify Node** takes the list of `FunctionalRequirement` objects and determines their type: `FR` (Functional), `NFR` (Non-Functional), or `BR` (Business Rule).

**Vision**: High-accuracy categorization that correctly identifies when a requirement is about system performance (NFR) vs. user behavior (FR).

## 2. Current Implementation (`classify.py`)
- **Logic**: 
    - Uses Gemini with `ClassificationResponse` pydantic model.
    - Categorizes every requirement with a confidence score.
- **Input**: `state.get("functional_requirements")`.
- **Output**: `{"classified_requirements": List[Classified]}` or `{"error": str}`.

## 3. Expected Enhancements (TODOs)
- [ ] **Multi-Label**: Support cases where a requirement might be both `FR` and `NFR` (e.g., "The system shall process orders in < 1s").
- [ ] **Confidence thresholds**: Flag classifications with `< 70%` confidence for manual review.
- [ ] **Context Awareness**: Use previous node outputs to gain more context for difficult classifications.

## 4. Operational Guidelines
- **Consistency**: The classification label should be consistent across identical requirements.
- **Fallback**: If the LLM fails, default to `FR` for any requirement that starts with "The system shall...".
- **Performance**: Ensure this classification step is fast as it's a key part of the extraction pipeline.

## 5. Verification Checklist
- [ ] Are all requirements classified as `FR`, `NFR`, or `BR`?
- [ ] Is every classification assigned a confidence score between 0 and 1?
- [ ] Does it handle empty input lists gracefully?
- [ ] Are labels (e.g., `FR`) capitalized correctly?
