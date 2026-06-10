from app.schemas.pipeline_state import PipelineState
from app.schemas.items import StructuredSummary
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate


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
        
        raw = await llm.ainvoke([
            ("system", "You are an expert business analyst. Provide a concise executive summary of the following document text."),
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
