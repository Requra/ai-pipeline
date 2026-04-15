import time
from app.schemas.pipeline_state import PipelineState

async def format_node(state: PipelineState) -> dict:
    """
    Assemble all outputs into final format and infer status.
    """
    print("--- FORMAT NODE ---")
    
    error = state.get("error")
    stories = state.get("user_stories", [])
    reqs = state.get("classified_requirements", [])
    
    if error and not stories and not reqs:
        status = "error"
    elif state.get("is_useful") is False:
        status = "rejected"
    elif error and (stories or reqs):
        status = "partial"
    else:
        status = "success"
        
    return {
        "status": status,
        "is_useful": state.get("is_useful", True),
        "relevance_score": state.get("relevance_score", 0.0),
        "error": error
    }
