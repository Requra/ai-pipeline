from app.schemas.pipeline_state import PipelineState
from app.schemas.items import ClassifiedRequirement

async def classify_node(state: PipelineState) -> dict:
    """
    Label each extracted requirement as Functional (FR), Non-Functional (NFR), or Business Rule (BR).
    """
    print("--- CLASSIFY NODE ---")
    frs = state.get("functional_requirements", [])
    
    if not frs:
        return {"classified_requirements": []}

    classified = []
    for fr in frs:
        # Mock classification logic
        label = "FR" if "log in" in fr.text.lower() else "NFR"
        confidence = 0.95
        
        req = ClassifiedRequirement(
            id=fr.id,
            text=fr.text,
            actor=fr.actor,
            goal=fr.goal,
            source_hint=fr.source_hint,
            label=label,
            confidence=confidence
        )
        classified.append(req)
        
    return {"classified_requirements": classified}
