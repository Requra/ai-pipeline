from app.schemas.pipeline_state import PipelineState
from app.config import settings
from app.nodes.repair_stories import REPAIRABLE_RULES

def route_after_ingest(state: PipelineState) -> str:
    """
    If there is an error OR the document is rejected by AI, go straight to format. 
    Otherwise route to transcribe if audio, else parse_to_chunks.
    """
    if state.get("error") or state.get("is_useful") is False:
        return "format"   # short-circuit: skip processing if useless or error

    return "transcribe" if state.get("file_type") == "audio" else "parse_to_chunks"


def route_after_quality_gate(state: PipelineState) -> str:
    """
    If ENABLE_QUALITY_REPAIR is true, and repair_attempts is less than MAX_REPAIR_ATTEMPTS,
    and there are repairable quality issues on stories that still exist, route to repair_stories.
    Otherwise go to summarize.
    """
    if not getattr(settings, "ENABLE_QUALITY_REPAIR", False):
        return "summarize"
        
    attempts = state.get("repair_attempts", 0)
    max_attempts = getattr(settings, "MAX_REPAIR_ATTEMPTS", 1)
    if attempts >= max_attempts:
        return "summarize"
        
    issues = state.get("quality_issues", []) or []
    stories = state.get("user_stories", []) or []
    story_ids = {s.id for s in stories}
    
    has_repairable = False
    for issue in issues:
        item_type = getattr(issue, "item_type", "") if hasattr(issue, "item_type") else issue.get("item_type", "")
        rule = getattr(issue, "rule_violated", "") if hasattr(issue, "rule_violated") else issue.get("rule_violated", "")
        details = getattr(issue, "details", "") if hasattr(issue, "details") else issue.get("details", "")
        
        if item_type == "story" and rule in REPAIRABLE_RULES:
            # Check if details contains one of the story IDs
            for s_id in story_ids:
                if s_id in details:
                    has_repairable = True
                    break
            if has_repairable:
                break
                
    return "repair_stories" if has_repairable else "summarize"
