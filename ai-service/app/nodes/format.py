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
    elif error and (stories or reqs):
        status = "partial"
    else:
        status = "success"
        
    return {
        "status": status,
        "error": error
    }
