import pytest
from app.nodes.generate import generate_node
from app.schemas.items import ClassifiedRequirement

@pytest.mark.asyncio
async def test_generate_node_real(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1, 
            text="The user must be able to log in.", 
            actor="User", 
            goal="login", 
            label="FR", 
            confidence=0.9
        )
    ]
    
    result = await generate_node(state)
    
    assert "user_stories" in result
    stories = result["user_stories"]
    assert len(stories) == 1
    assert "log in" in stories[0].description.lower()
    assert len(stories[0].acceptance_criteria) > 0
