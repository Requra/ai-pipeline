from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

class StoryResponse(BaseModel):
    stories: List[UserStory] = Field(description="A list of generated user stories.")

async def generate_node(state: PipelineState) -> dict:
    """
    Transform each classified requirement into exactly one user story using Gemini.
    """
    print("--- GENERATE NODE ---")
    classified_reqs = state.get("classified_requirements", [])
    
    if not classified_reqs:
        return {"user_stories": []}

    try:
        # Get Gemini LLM
        llm = get_llm()
        
        # Define structured output
        structured_llm = llm.with_structured_output(StoryResponse)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert product manager. Transform each categorized requirement into a user story with detailed acceptance criteria (Given-When-Then format where appropriate). Ensure a 1:1 mapping using the source requirement ID."),
            ("user", "{items}")
        ])
        
        chain = prompt | structured_llm
        response = await chain.ainvoke({"items": [f"ID {getattr(req, 'id', 0)}: {getattr(req, 'text', '')} ({getattr(req, 'label', 'FR')})" for req in classified_reqs]})
        
        stories = response.stories if response else []
        
        # Enforce 1:1 mapping check
        if len(stories) != len(classified_reqs):
            print(f"Warning: Story count mismatch ({len(stories)} stories vs {len(classified_reqs)} requirements)")
            
        return {"user_stories": stories}
        
    except Exception as e:
        print(f"Generate node LLM failure: {e}")
        # Fallback to simple logic for resilience
        results = []
        for req in classified_reqs:
            results.append(UserStory(
                title=f"Story for {getattr(req, 'id', 0)}",
                description=f"As a {getattr(req, 'actor', 'User')}, I want {getattr(req, 'goal', 'to do something')}.",
                acceptance_criteria=[AcceptanceCriterion(text="Works as expected", criterion_type="plain")],
                source_fr_id=getattr(req, 'id', 0),
                label=getattr(req, 'label', 'FR')
            ))
        return {"user_stories": results, "error": f"GENERATE_LLM_FAILURE: {str(e)}"}
