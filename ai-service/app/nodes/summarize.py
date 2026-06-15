from app.schemas.pipeline_state import PipelineState
from app.schemas.items import StructuredSummary
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId


async def summarize_node(state: PipelineState) -> dict:
    """
    Generate structured summary matching `StructuredSummary` contract.
    """
    print("--- SUMMARIZE NODE ---")
    raw_text = state.get("raw_text", "")

    if not raw_text:
        return {"summary": StructuredSummary(
            executive_summary="",
            key_decisions=[],
            open_questions=[],
            risks=[],
            assumptions=[],
            action_items=[],
            stakeholders=[],
            scope=[],
            out_of_scope=[]
        )}

    # Attempt LLM summarization, but fall back to deterministic structured mock
    try:
        llm = get_llm()
        
        system_prompt = load_prompt(PromptId.SUMMARIZE_STRUCTURED_V1)
        raw = await llm.ainvoke([
            ("system", system_prompt),
            ("user", f"Summarize this text:\n\n{raw_text}")
        ])

        # If LLM returns free text, place into executive_summary and leave others empty
        content = getattr(raw, "content", None) or str(raw)
        return {"summary": StructuredSummary(
            executive_summary=content,
            key_decisions=[],
            open_questions=[],
            risks=[],
            assumptions=[],
            action_items=[],
            stakeholders=[],
            scope=[],
            out_of_scope=[]
        )}


    except Exception as e:
        print(f"Summarize node LLM failure: {e}")
        return {"summary": StructuredSummary(
            executive_summary=(raw_text[:300] + "...") if raw_text else "",
            key_decisions=[],
            open_questions=[],
            risks=[],
            assumptions=[],
            action_items=[],
            stakeholders=[],
            scope=[],
            out_of_scope=[]
        )}
