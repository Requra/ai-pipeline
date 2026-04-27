from app.schemas.pipeline_state import PipelineState
from app.schemas.items import ClassifiedRequirement
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Literal

# Generated prompt for the task 
# We separated System and User Prompt from the prompt message in  prompt = ChatPromptTemplate.from_messages for easy tracing **later**
System_Prompt = """
You are a senior requirements analyst.

Classify each requirement into exactly one label:

- FR = Functional Requirement
- NFR = Non-Functional Requirement
- BR = Business Rule

Definitions:

FR:
A required system behavior, feature, capability, workflow step, or user/system interaction.
It describes what the system or user should do.

NFR:
A quality attribute or operational constraint.
It describes how well the system should perform or under what quality constraints it must operate.
Examples include performance, security controls, reliability, availability, scalability, usability, maintainability, recoverability, and accessibility.

BR:
A business policy, domain rule, eligibility rule, compliance rule, restriction, permission rule, or business validation rule.
It describes what is allowed, required, or prohibited by business logic, domain policy, or regulation.

Classification rules:
- Choose FR if the main meaning is a feature, action, capability, workflow, or interaction.
- Choose NFR if the main meaning is performance, security, reliability, availability, scalability, usability, maintainability, recoverability, accessibility, or technical/operational quality constraints.
- Choose BR if the main meaning is a policy, restriction, permission, eligibility rule, approval rule, compliance rule, or domain/business validation rule.
- If a requirement contains mixed signals, choose the dominant meaning.
- If a statement describes both a business rule and a system action enforcing that rule, choose BR when the business rule is the core meaning, otherwise choose FR.
- If a statement describes a technical security or performance constraint, choose NFR.
- If a statement describes who is allowed, required, eligible, restricted, or prohibited, prefer BR.
- Use text, actor, goal, and source_hint as context.

Ambiguity handling:
- Some requirements may reasonably fit more than one category.
- You must still return exactly one label.
- In ambiguous cases, select the best overall label and reduce confidence.

Output rules:
- Return exactly one result for each input ID.
- Do not omit IDs.
- Do not invent IDs.
- Labels must be exactly: FR, NFR, BR.

Confidence rules:
- Use the full confidence range honestly.
- Do not assign the same confidence to all items unless they are equally clear.
- Lower confidence for short, vague, mixed, or ambiguous requirements.
- Higher confidence only when the classification is very clear.

Confidence scale:
- 0.90 to 1.00 = very clear
- 0.75 to 0.89 = clear
- 0.50 to 0.74 = somewhat ambiguous
- below 0.50 = very unclear
"""

User_Prompt="""
Classify the following requirements:
{items}
"""

# We only ask the model for the fields it must decide:
# id, label, and confidence 
class RequirementClassification(BaseModel):
    id: int = Field(description="Requirement ID")
    label: Literal["FR", "NFR", "BR"] = Field(description="Single classification label")
    confidence: float = Field(description="Confidence score between 0 and 1")

class ClassificationResponse(BaseModel):
    classifications: List[RequirementClassification] = Field(description="A list of classified requirements.")

# Helper function to convert one requirement into a prompt-friendly string
def _format_requirement(fr) -> str:
    return (
        f"id: {fr.id}\n"
        f"text: {fr.text}\n"
        f"actor: {fr.actor}\n"
        f"goal: {fr.goal}\n"
        f"source_hint: {fr.source_hint}")

# Helper function to ensure confidence is always a valid float between 0 and 1
# If parsing fails, we return a neutral default value of 0.5
def _clamp_confidence(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, value))

# spliting requirements into chunks as for later will be sent in batches
def _chunk_requirements(requirements, batch_size: int = 5):
    for i in range(0, len(requirements), batch_size):
        yield requirements[i:i + batch_size]

# sending bactches ine by one
async def _classify_batch(chain, batch) -> ClassificationResponse:
    items = "\n\n".join(_format_requirement(fr) for fr in batch)
    return await chain.ainvoke({"items": items})


# main node 
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
        llm = get_llm("gpt-oss-20b")
        
        # Define structured output
        structured_llm = llm.with_structured_output(ClassificationResponse)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system",System_Prompt ),
            ("user", User_Prompt)
        ])
        
        chain = prompt | structured_llm
        all_classifications = []

        # processing batch by batch
        for batch in _chunk_requirements(frs, batch_size=5):
            response = await _classify_batch(chain, batch)
            # If the batch returned structured classifications, save them
            if response and response.classifications:
                all_classifications.extend(response.classifications)
        # Format all requirements into one string for the prompt
        # items = "\n\n".join(_format_requirement(fr) for fr in frs)
        # response = await chain.ainvoke({"items": items})
        # response_map = {item.id: item for item in response.classifications}
         # lookup map by requirement id
        response_map = {item.id: item for item in all_classifications}
        classified = []
        for fr in frs:
            item = response_map.get(fr.id)
            #skip if item is missing
            if not item:
                continue

            classified.append(
                ClassifiedRequirement(
                    id=fr.id,
                    text=fr.text,
                    actor=fr.actor,
                    goal=fr.goal,
                    source_hint=fr.source_hint,
                    label=item.label,
                    confidence=_clamp_confidence(item.confidence),
                )
            )

        return {"classified_requirements": classified}



        # classified = response.classifications if response else []
        
        # return {"classified_requirements": classified}
        
        #  Fallback will be discussed in the meeting --> we can use classifier model+ similarity 
    except Exception as e:
        print(f"Classify node LLM failure: {e}")
        # Fallback to simple logic for resilience
        # results = []
        # for fr in frs:
        #     # We use attribute access since it might be a Pydantic object
        #     text = fr.text if hasattr(fr, 'text') else fr.get('text', '')
        #     label = "FR" if "log in" in text.lower() else "NFR"
        #     results.append(ClassifiedRequirement(
        #         id=getattr(fr, 'id', 0),
        #         text=text,
        #         actor=getattr(fr, 'actor', 'User'),
        #         goal=getattr(fr, 'goal', ''),
        #         source_hint=getattr(fr, 'source_hint', ''),
        #         label=label, 
        #         confidence=0.8
        #     ))
        # return {"classified_requirements": results, "error": f"CLASSIFY_LLM_FAILURE: {str(e)}"}



