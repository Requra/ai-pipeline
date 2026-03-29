from app.schemas.pipeline_state import PipelineState
from app.schemas.items import ClassifiedRequirement
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

class ClassificationResponse(BaseModel):
    classifications: List[ClassifiedRequirement] = Field(description="A list of classified requirements.")

async def classify_node(state: PipelineState) -> dict:
    """
    Categorize each extracted requirement as Functional (FR), Non-Functional (NFR), or Business Rule (BR).
    """
    print("--- CLASSIFY NODE ---")
    frs = state.get("functional_requirements", [])
    
    if not frs:
        return {"classified_requirements": []}

    try:
        # Get Gemini LLM
        llm = get_llm()
        
        # Define structured output
        structured_llm = llm.with_structured_output(ClassificationResponse)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert analyst. Classify each provided functional requirement into one of the following categories: FR (Functional), NFR (Non-Functional), or BR (Business Rule). Assign a confidence score."),
            ("user", "{items}")
        ])
        
        chain = prompt | structured_llm
        response = await chain.ainvoke({"items": [f"ID {fr.id}: {fr.text}" for fr in frs]})
        
        classified = response.classifications if response else []
        
        return {"classified_requirements": classified}
        
    except Exception as e:
        print(f"Classify node LLM failure: {e}")
        # Fallback to simple logic for resilience
        results = []
        for fr in frs:
            # We use attribute access since it might be a Pydantic object
            text = fr.text if hasattr(fr, 'text') else fr.get('text', '')
            label = "FR" if "log in" in text.lower() else "NFR"
            results.append(ClassifiedRequirement(
                id=getattr(fr, 'id', 0),
                text=text,
                actor=getattr(fr, 'actor', 'User'),
                goal=getattr(fr, 'goal', ''),
                source_hint=getattr(fr, 'source_hint', ''),
                label=label, 
                confidence=0.8
            ))
        return {"classified_requirements": results, "error": f"CLASSIFY_LLM_FAILURE: {str(e)}"}
