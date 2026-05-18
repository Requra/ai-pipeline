from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List


# ---------------- PROMPT ----------------

SYSTEM_PROMPT = """
You are a senior product manager and software analyst.

Convert requirements into USER STORIES.

RULES:
1. Each requirement → exactly ONE user story (1:1 mapping)
2. A requirement may have MULTIPLE labels (FR, NFR, BR)
3. Include ALL concerns in ONE story (do NOT split)
4. Keep requirement id mapping
5. Write Agile format:
   As a <actor>, I want <goal>, so that <benefit>
6. Generate clear acceptance criteria
"""

USER_PROMPT = """
Convert these classified requirements into user stories:

{items}
"""


# ---------------- STRUCTURED OUTPUT ----------------

class StoryResponse(BaseModel):
    id: int
    title: str
    description: str
    acceptance_criteria: List[str]
    labels: List[str]   #  IMPORTANT FOR MULTI-LABEL SUPPORT


class GenerationResponse(BaseModel):
    stories: List[StoryResponse] = Field(description="Generated user stories")


# ---------------- HELPERS ----------------

def _format_requirement(req) -> str:
    labels = getattr(req, "labels", None) or [getattr(req, "label", "FR")]

    return (
        f"id: {req.id}\n"
        f"text: {req.text}\n"
        f"actor: {req.actor}\n"
        f"goal: {req.goal}\n"
        f"labels: {labels}\n"
        f"source_hint: {req.source_hint}"
    )


def _normalize_labels(labels):
    if isinstance(labels, list):
        return labels
    if isinstance(labels, str):
        return [labels]
    return ["FR"]


# ---------------- NODE ----------------

async def generate_node(state: PipelineState) -> dict:
    print("--- GENERATE NODE (MULTI-LABEL) ---")

    classified = state.get("classified_requirements", [])
    if not classified:
        return {"user_stories": []}

    try:
        llm = get_llm()

        if llm is None:
            raise RuntimeError("LLM not initialized")

        structured_llm = llm.with_structured_output(
            GenerationResponse,
            method="function_calling"
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("user", USER_PROMPT)
        ])

        chain = prompt | structured_llm

        items_text = "\n\n".join(_format_requirement(req) for req in classified)

        response = await chain.ainvoke({"items": items_text})

        stories = response.stories if response else []

        final_stories = []

        for s in stories:
            final_stories.append(
                UserStory(
                    title=s.title,
                    description=s.description,
                    acceptance_criteria=[
                        AcceptanceCriterion(
                            text=c,
                            criterion_type="Given-When-Then" if "Given" in c else "plain"
                        )
                        for c in s.acceptance_criteria
                    ],
                    source_fr_id=s.id,

                    # fix 
                    labels=_normalize_labels(getattr(s, "labels", ["FR"]))
                )
            )

        return {"user_stories": final_stories}

    except Exception as e:
        print(f"Generate node LLM failure: {e}")

        # ---------------- SAFE FALLBACK ----------------
        fallback = []

        for req in classified:
            labels = _normalize_labels(getattr(req, "labels", None))

            fallback.append(
                UserStory(
                    title=f"Story for requirement {req.id}",
                    description=f"As a {req.actor}, I want {req.goal}, so that: {req.text}",
                    acceptance_criteria=[
                        AcceptanceCriterion(
                            text="Requirement is implemented as specified",
                            criterion_type="plain"
                        )
                    ],
                    source_fr_id=req.id,

                    # FIX 
                    labels=labels
                )
            )

        return {
            "user_stories": fallback,
            "error_message": str(e),
            "status": "partial"
        }
