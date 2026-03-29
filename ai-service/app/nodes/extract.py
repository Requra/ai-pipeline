from app.schemas.pipeline_state import PipelineState
from app.schemas.items import FunctionalRequirement
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

class ExtractionResponse(BaseModel):
    requirements: List[FunctionalRequirement] = Field(description="A list of extracted functional requirements.")

async def extract_node(state: PipelineState) -> dict:
    """
    Send raw text to Gemini and extract Functional Requirements using structured output.
    """
    print("--- EXTRACT NODE ---")
    raw_text = state.get("raw_text")
    
    if not raw_text:
        return {"error": "EXTRACT_FAILED: no raw text provided"}

    try:
        # Get Gemini LLM
        llm = get_llm()
        
        # Define structured output
        structured_llm = llm.with_structured_output(ExtractionResponse)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert requirements engineer. Extract a list of functional requirements from the provided document text. Each requirement should have an ID (starting from 1), text, actor, and goal."),
            ("user", "{text}")
        ])
        
        chain = prompt | structured_llm
        response = await chain.ainvoke({"text": raw_text})
        
        frs = response.requirements if response else []
        
        if not frs:
            return {"functional_requirements": [], "error": "EXTRACT_EMPTY: no functional requirements found"}
            
        return {"functional_requirements": frs}
        
    except Exception as e:
        print(f"Extract node LLM failure: {e}")
        # Fallback to simple logic for resilience
        return {
            "functional_requirements": [
                FunctionalRequirement(id=1, text="The system shall allow users to browse products.", actor="User", goal="browse products", source_hint="browse")
            ],
            "error": f"EXTRACT_LLM_FAILURE: {str(e)}"
        }
