import pytest
from app.nodes.summarize import summarize_node

@pytest.mark.asyncio
async def test_summarize_node_real(base_state):
    state = base_state.copy()
    state["raw_text"] = "The project aims to build a mobile app for budget tracking. Users can add expenses, set limits, and view charts. Security is priority. We decided to use Firebase."
    
    result = await summarize_node(state)
    
    assert "summary" in result
    assert result["summary"] is not None
    assert len(result["summary"]) > 20
    # Summary should mention Firebase or budget tracking
    text = result["summary"].lower()
    assert "firebase" in text or "budget" in text or "mobile" in text
