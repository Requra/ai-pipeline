# Node Guide: Summarize Node
**Status**: `[UNASSIGNED]`  
**Owner Role**: AI Engineer / Content Specialist

## 1. Description & Vision
The **Summarize Node** creates a concise, high-level "Executive Summary" of the source document or audio. It highlights the core project scope, main technical challenges, and key stakeholder requests.

**Vision**: A summary that is both brief and rich in context, providing an immediate overview for management and developers alike.

## 2. Current Implementation (`summarize.py`)
- **Logic**: 
    - Uses Gemini to generate an executive summary.
    - Focuses on key decisions, open questions, and stakeholder pain points.
- **Input**: `state.get("raw_text")`.
- **Output**: `{"summary": str}` or `{"error": str}`.

## 3. Expected Enhancements (TODOs)
- [ ] **Technical Recap**: Add a section specifically for technical risks identified in the text.
- [ ] **Open Questions**: Explicitly list all "Open Questions" found in the source document.
- [ ] **Stakeholder Map**: Identify and categorize stakeholders (e.g., "Customer", "Project Lead").
- [ ] **Formatting**: Ensure the summary uses Markdown headings and bullet points for readability.

## 4. Operational Guidelines
- **Conciseness**: The executive summary should not exceed 500 words.
- **Accuracy**: Ensure the summary does not hallucinate new features; it must strictly adhere to the source text.
- **Tone**: Use a professional, analytical tone throughout the summary.

## 5. Verification Checklist
- [ ] Is the summary logically structured with headers?
- [ ] Does it correctly capture the document's main goal?
- [ ] Is the output consistent across different runs (using fixed temperature)?
- [ ] Does it handle cases where `raw_text` is missing or very short?
