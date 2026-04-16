import pytest
from app.nodes.format import format_node

@pytest.mark.asyncio
async def test_format_node_success(base_state):
    state = base_state.copy()
    state["raw_text"] = "Sample project text"
    state["user_stories"] = [{"title": "Story 1"}]
    state["classified_requirements"] = [{"id": 1, "label": "FR"}]
    state["summary"] = "Project summary"
    
    result = await format_node(state)
    
    assert result["status"] == "success"
    assert result["is_useful"] is True
    assert "error" in result


@pytest.mark.asyncio
async def test_format_node_error(base_state):
    state = base_state.copy()
    state["error"] = "Something went wrong"
    
    result = await format_node(state)
    
    assert result["status"] == "error"
