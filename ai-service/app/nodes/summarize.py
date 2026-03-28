from app.schemas.pipeline_state import PipelineState

async def summarize_node(state: PipelineState) -> dict:
    """
    Generate an executive summary highlighting key decisions, open questions, and pain points.
    Runs in parallel with Format.
    """
    print("--- SUMMARIZE NODE ---")
    raw_text = state.get("raw_text", "")
    
    if not raw_text or len(raw_text.split()) < 200:
        return {"summary": "Short execution summary based on available text."}

    summary = """## Executive Summary
This is a standard multi-sentence overview of the pipeline execution.

## Key Decisions
- Mocked processing applied.

## Open Questions
- None identified.

## Stakeholder Pain Points
- No explicit issues resolved manually.
"""
    return {"summary": summary}
