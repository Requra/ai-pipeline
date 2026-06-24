from typing import Dict, Any

# In-memory database of running job processes
progress_store: Dict[str, Dict[str, Any]] = {}

def update_progress(
    job_id: str, 
    node_name: str, 
    progress_pct: int, 
    status: str = "processing", 
    result: Any = None, 
    error: Any = None
):
    """
    Thread-safe progress updates for the AI pipeline.
    """
    if not job_id:
        return
    
    if job_id not in progress_store:
        progress_store[job_id] = {
            "status": "processing",
            "progress_pct": 0,
            "current_node": "started",
            "result": None,
            "error": None
        }
        
    progress_store[job_id].update({
        "status": status,
        "progress_pct": progress_pct,
        "current_node": node_name
    })
    
    if result is not None:
        progress_store[job_id]["result"] = result
    if error is not None:
        progress_store[job_id]["error"] = error
