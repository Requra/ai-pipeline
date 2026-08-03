import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.nodes.summarize import summarize_node
from app.schemas.items import ClassifiedRequirement, StructuredSummary
from app.schemas.items import SourceChunk


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


@pytest.mark.asyncio
async def test_summarize_preserves_multiple_document_scopes(base_state):
    state = base_state.copy()
    state["raw_text"] = ""
    state["source_documents"] = [
        {"document_id": "workspace", "filename": "workspace.docx"},
        {"document_id": "operations", "filename": "operations.pdf"},
    ]
    state["chunks"] = [
        SourceChunk(chunk_id="c1", text="Workspace owners invite collaborators.", start_char=0, end_char=38, document_id="workspace"),
        SourceChunk(chunk_id="c2", text="Operations analysts manage support cases.", start_char=0, end_char=40, document_id="operations"),
    ]
    response = StructuredSummary(
        executive_summary="The workspace document covers collaboration; the operations document covers case management.",
        key_decisions=[], open_questions=[], risks=[], assumptions=[], action_items=[],
        stakeholders=["workspace owner", "operations analyst"],
        scope=["workspace collaboration", "case management"], out_of_scope=[],
    ).model_dump_json()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=response))

    with patch("app.nodes.summarize.get_llm", return_value=mock_llm):
        result = await summarize_node(state)

    assert mock_llm.ainvoke.await_count == 3
    final_prompt = mock_llm.ainvoke.await_args_list[-1].args[0][1][1]
    assert "workspace.docx" in final_prompt
    assert "operations.pdf" in final_prompt
    assert "workspace" in result["summary"].executive_summary.lower()
    assert "operations" in result["summary"].executive_summary.lower()


@pytest.mark.asyncio
async def test_summary_removes_none_sentinel_and_restores_omitted_requirements(base_state):
    state = base_state.copy()
    state["raw_text"] = "The service exports reports and maintains 99.9% uptime."
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="The service shall export monthly reports.",
            candidate_labels=["FR"],
            labels=["FR"],
            confidence=0.9,
        ),
        ClassifiedRequirement(
            id=2,
            text="The service shall maintain 99.9% monthly uptime.",
            candidate_labels=["NFR"],
            labels=["NFR"],
            confidence=0.9,
        ),
    ]
    response = StructuredSummary(
        executive_summary="The service exports monthly reports.",
        key_decisions=[], open_questions=["None"], risks=[], assumptions=[],
        action_items=[], stakeholders=[], scope=["Monthly report export"],
        out_of_scope=[],
    ).model_dump_json()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=response))

    with patch("app.nodes.summarize.get_llm", return_value=mock_llm):
        result = await summarize_node(state)

    assert result["summary"].open_questions == []
    assert any("99.9%" in item for item in result["summary"].scope)


@pytest.mark.asyncio
async def test_summary_restores_explicit_stakeholders_and_key_constraints(base_state):
    state = base_state.copy()
    state["raw_text"] = "Administrators configure access. Availability is 99.9%."
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1,
            text="Administrators shall configure access.",
            actor="Administrator",
            candidate_labels=["FR"], labels=["FR"], confidence=0.9,
        ),
        ClassifiedRequirement(
            id=2,
            text="The service shall maintain 99.9% monthly uptime.",
            actor="System",
            candidate_labels=["NFR"], labels=["NFR"], confidence=0.9,
        ),
    ]
    response = StructuredSummary(
        executive_summary="The service manages access.",
        key_decisions=[], open_questions=[], risks=[], assumptions=[],
        action_items=[], stakeholders=[], scope=[], out_of_scope=[],
    ).model_dump_json()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=response))

    with patch("app.nodes.summarize.get_llm", return_value=mock_llm):
        result = await summarize_node(state)

    assert "Administrators" in result["summary"].stakeholders
    assert "99.9%" in result["summary"].executive_summary


@pytest.mark.asyncio
async def test_summary_consolidates_user_role_aliases(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1, text="Users shall submit requests.", actor="User",
            candidate_labels=["FR"], labels=["FR"], confidence=0.9,
        ),
        ClassifiedRequirement(
            id=2, text="Standard users shall view requests.", actor="Standard User",
            candidate_labels=["FR"], labels=["FR"], confidence=0.9,
        ),
    ]
    response = StructuredSummary(
        executive_summary="Users submit and view requests.",
        key_decisions=[], open_questions=[], risks=[], assumptions=[],
        action_items=[], stakeholders=["Users"], scope=[], out_of_scope=[],
    ).model_dump_json()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=response))

    with patch("app.nodes.summarize.get_llm", return_value=mock_llm):
        result = await summarize_node(state)

    assert result["summary"].stakeholders == ["Users"]


@pytest.mark.asyncio
async def test_summary_removes_invented_fields_and_verbose_stakeholder_duplicates(base_state):
    state = base_state.copy()
    state["classified_requirements"] = [
        ClassifiedRequirement(
            id=1, text="Users shall request asset checkout.", actor="User",
            candidate_labels=["FR"], labels=["FR"], confidence=0.9,
        ),
        ClassifiedRequirement(
            id=2, text="Managers shall approve checkout requests above $1,000.", actor="Manager",
            candidate_labels=["BR"], labels=["BR"], confidence=0.9,
        ),
    ]
    response = StructuredSummary(
        executive_summary="Users request assets and managers approve high-value requests.",
        key_decisions=["Manager approval is required above $1,000."],
        open_questions=[], risks=[],
        assumptions=["Users will receive necessary training."],
        action_items=["Develop and test the entire platform."],
        stakeholders=[
            "Users requesting asset checkout",
            "Users",
            "Managers approving requests",
            "Managers",
        ],
        scope=[],
        out_of_scope=["Changing the identity provider configuration."],
    ).model_dump_json()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=response))

    with patch("app.nodes.summarize.get_llm", return_value=mock_llm):
        result = await summarize_node(state)

    summary = result["summary"]
    assert summary.stakeholders == ["Users", "Managers"]
    assert summary.assumptions == []
    assert summary.action_items == []
    assert summary.out_of_scope == []
    assert summary.key_decisions == ["Manager approval is required above $1,000."]
