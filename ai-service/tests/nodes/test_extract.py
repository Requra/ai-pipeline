import pytest
from app.nodes.extract import extract_node

@pytest.mark.asyncio
async def test_extract_node_real(base_state):
    state = base_state.copy()
    state["raw_text"] = "The system shall process payments. The system must support user login. Performance must be under 2s."
    
    result = await extract_node(state)
    
    assert "functional_requirements" in result
    reqs = result["functional_requirements"]
    assert len(reqs) >= 2
    # Verify Pydantic objects or dicts have expected fields
    assert any("login" in str(req).lower() for req in reqs)
    assert any("payment" in str(req).lower() for req in reqs)
