from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion

async def generate_node(state: PipelineState) -> dict:
    """
    Transform each classified requirement into exactly one user story.
    Strict 1-to-1 mapping via source_fr_id.
    """
    print("--- GENERATE NODE ---")
    classified_reqs = state.get("classified_requirements", [])
    
    if not classified_reqs:
        return {"user_stories": []}

    stories = []
    try:
        for req in classified_reqs:
            # Mock generating story for req
            # Real logic would use templates based on req.label and prompt the LLM
            desc = f"As a {req.actor or 'User'}, I want {req.goal or 'to do something'} so that benefit."
            if req.label == "NFR":
                desc = f"The system shall {req.text} so that quality goal."
            elif req.label == "BR":
                desc = f"Given constraint, the system shall apply rule."
                
            story = UserStory(
                title=f"Story for FR {req.id}",
                description=desc,
                acceptance_criteria=[
                    AcceptanceCriterion(text="Criterion 1", criterion_type="Given-When-Then"),
                    AcceptanceCriterion(text="Criterion 2", criterion_type="plain")
                ],
                source_fr_id=req.id,
                label=req.label
            )
            stories.append(story)
            
        # Enforce 1:1 mapping
        if len(stories) != len(classified_reqs):
            return {"error": "GENERATE_MISMATCH: LLM generated count does not match input count"}
            
        return {"user_stories": stories}
    except Exception as e:
        return {"error": f"GENERATE_FAILED: {str(e)}"}
