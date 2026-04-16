import pytest
from app.nodes.classify import classify_node
from app.schemas.items import FunctionalRequirement

@pytest.mark.asyncio
async def test_classify_node_real(base_state):
    state = base_state.copy()
    state["functional_requirements"] = [
        FunctionalRequirement(id=1, text="The user must be able to log in.", actor="User", goal="login"),
        FunctionalRequirement(id=2, text="The system should respond in 200ms.", actor="System", goal="performance")
    ]
    
    result = await classify_node(state)
    
    assert "classified_requirements" in result
    classified = result["classified_requirements"]
    assert len(classified) == 2
    
    # Check if Gemini correctly labeled them
    labels = [c.label for c in classified]
    assert "FR" in labels
    assert "NFR" in labels or "BR" in labels
