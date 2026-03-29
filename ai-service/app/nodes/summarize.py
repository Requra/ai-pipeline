from app.schemas.pipeline_state import PipelineState
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
import os

async def summarize_node(state: PipelineState) -> dict:
    """
    Generate an executive summary highlighting key decisions, open questions, and pain points.
    """
    print("--- SUMMARIZE NODE ---")
    raw_text = state.get("raw_text", "")
    
    if not raw_text:
        return {"summary": "No text provided for summary."}

    # Use Gemini if available, otherwise mock
    try:
        # Get Gemini LLM
        llm = get_llm()
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert business analyst. Provide a concise executive summary of the following document text. Focus on key decisions, open questions, and stakeholder pain points."),
            ("user", "{text}")
        ])
        
        chain = prompt | llm
        response = await chain.ainvoke({"text": raw_text})
        
        return {"summary": response.content}
        
    except Exception as e:
        print(f"Summarize node LLM failure: {e}")
        return {"summary": f"## Executive Summary (Mocked)\n{raw_text[:200]}..."}
