import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.nodes.summarize import summarize_node
from app.schemas.items import StructuredSummary


@pytest.mark.asyncio
async def test_summarize_node_real(base_state):
    state = base_state.copy()
    state["raw_text"] = "The project aims to build a mobile app for budget tracking. Users can add expenses, set limits, and view charts. Security is priority. We decided to use Firebase."

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="Executive summary: The project builds a mobile app for budget tracking using Firebase."))

    with patch("app.nodes.summarize.get_llm", return_value=mock_llm):
        result = await summarize_node(state)

    assert "summary" in result
    assert result["summary"] is not None

    summary = result["summary"]
    if isinstance(summary, StructuredSummary):
        text = summary.executive_summary or ""
    else:
        text = str(summary)

    assert len(text) > 20
    # Summary should mention Firebase or budget tracking
    text_lower = text.lower()
    assert "firebase" in text_lower or "budget" in text_lower or "mobile" in text_lower
