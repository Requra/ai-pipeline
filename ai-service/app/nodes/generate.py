from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion, RequirementCoverage
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Any
import json


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

Return JSON exactly in this shape:
{
  "stories": [
    {
      "id": 1,
      "title": "Register account",
      "description": "As a user, I want to register using email and password, so that I can access the CRM.",
      "acceptance_criteria": [
        "Given a new user, when they submit valid email and password, then the account is created."
      ],
      "labels": ["FR"]
    }
  ]
}

Do NOT return `user_stories`, markdown, explanation, or plain text.
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

def normalize_generation_payload(parsed: Any) -> dict:
    if isinstance(parsed, list):
        return {"stories": parsed}

    if not isinstance(parsed, dict):
        raise ValueError(f"Generation output must be dict or list, got {type(parsed).__name__}")

    if "stories" in parsed:
        return parsed

    if "user_stories" in parsed:
        return {"stories": parsed["user_stories"]}

    if "items" in parsed:
        return {"stories": parsed["items"]}

    if "data" in parsed:
        return {"stories": parsed["data"]}

    return parsed

def _format_requirement(req) -> str:
    labels = getattr(req, "labels", None) or [getattr(req, "label", "FR")]
    evidence = getattr(req, "evidence", [])

    return (
        f"id: {req.id}\n"
        f"text: {req.text}\n"
        f"actor: {req.actor}\n"
        f"goal: {req.goal}\n"
        f"labels: {labels}\n"
        f"candidate_labels: {getattr(req, 'candidate_labels', [])}\n"
        f"evidence: {evidence}"
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
        new_warnings = [
            {"node_name": "generate", "code": "GENERATE_SKIPPED_NO_REQUIREMENTS", "message": "No classified requirements available; generation skipped."}
        ]
        existing_warnings = state.get("warnings", []) or []
        return {"user_stories": [], "warnings": existing_warnings + new_warnings}

    to_generate = []
    to_skip = []
    non_story_types = {"Open Question", "Out-of-Scope", "Assumption"}

    for req in classified:
        labels = _normalize_labels(getattr(req, "labels", None))
        # If labels are exclusively non-story types, skip generation
        if set(labels).issubset(non_story_types):
            to_skip.append(req)
        else:
            to_generate.append(req)

    requirement_coverages = []
    for req in to_skip:
        coverage = RequirementCoverage(
            requirement_id=req.id,
            coverage_type="non_story_requirement",
            story_ids=[],
            acceptance_criteria_ids=[],
            reason="Open questions/out-of-scope/assumptions are not converted into user stories."
        )
        requirement_coverages.append(coverage)

    if not to_generate:
        new_warnings = [
            {"node_name": "generate", "code": "GENERATE_SKIPPED_NO_ACTIONABLE", "message": "No actionable requirements available for generation; skipped."}
        ]
        existing_warnings = state.get("warnings", []) or []
        return {
            "user_stories": [], 
            "warnings": existing_warnings + new_warnings,
            "requirement_coverages": requirement_coverages
        }


    try:
        llm = get_llm()

        if llm is None:
            raise RuntimeError("LLM not initialized")

        items_text = "\n\n".join(_format_requirement(req) for req in to_generate)

        raw = await llm.ainvoke([
            ("system", SYSTEM_PROMPT),
            ("user", USER_PROMPT.format(items=items_text))
        ])
        content = getattr(raw, "content", None) or str(raw)

        # Strip common code fences
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        try:
            parsed = json.loads(content)
            normalized = normalize_generation_payload(parsed)
            response = GenerationResponse.model_validate(normalized)
        except Exception as pe:
            print(f"Generation parse/validation error: {pe}")
            print(f"Raw content: {content}")
            raise pe

        stories = response.stories if response else []

        final_stories = []

        for s in stories:
            story_id = f"{state.get('job_id')}_story_{s.id}"
            user_story = UserStory(
                id=story_id,
                title=s.title,
                description=s.description,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="",
                        text=c,
                        criterion_type="Given-When-Then" if "Given" in c else "plain"
                    )
                    for c in s.acceptance_criteria
                ],
                source_requirement_ids=[s.id],
                labels=_normalize_labels(getattr(s, "labels", ["FR"])),
                evidence_reference=[]
            )

            # create coverage record for this requirement
            coverage = RequirementCoverage(
                requirement_id=s.id,
                coverage_type="covered_by_story",
                story_ids=[story_id],
                acceptance_criteria_ids=[c.id for c in user_story.acceptance_criteria],
                reason=None
            )

            final_stories.append(user_story)
            requirement_coverages.append(coverage)
            
        result = {"user_stories": final_stories, "requirement_coverages": requirement_coverages}
        return result

    except Exception as e:
        print(f"Generate node LLM failure: {e}")

        # ---------------- SAFE FALLBACK ----------------
        fallback = []

        for req in to_generate:
            labels = _normalize_labels(getattr(req, "labels", None))
            story_id = f"{state.get('job_id')}_story_{req.id}"
            
            actor = getattr(req, "actor", None)
            goal = getattr(req, "goal", None)
            
            if not actor:
                req_text_lower = req.text.lower()
                if "admin" in req_text_lower:
                    actor = "admin"
                elif "sales representative" in req_text_lower:
                    actor = "sales representative"
                elif "viewer" in req_text_lower:
                    actor = "viewer"
                elif set(labels).issubset({"NFR", "BR"}):
                    actor = "system"
                else:
                    actor = "user"
            
            if not goal:
                goal = "satisfy this requirement"

            fallback_story = UserStory(
                id=story_id,
                title=f"Story for requirement {req.id}",
                description=f"As a {actor}, I want {goal}, so that: {req.text}",
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="",
                        text="Requirement is implemented as specified",
                        criterion_type="plain"
                    )
                ],
                source_requirement_ids=[req.id],
                labels=labels,
                evidence_reference=getattr(req, "evidence", [])
            )

            fallback.append(fallback_story)

            coverage = RequirementCoverage(
                requirement_id=req.id,
                coverage_type="covered_by_story",
                story_ids=[story_id],
                acceptance_criteria_ids=[c.id for c in fallback_story.acceptance_criteria],
                reason=None
            )
            requirement_coverages.append(coverage)

        result = {"user_stories": fallback, "requirement_coverages": requirement_coverages}

        existing_warnings = state.get("warnings", []) or []
        new_warnings = [
            {
                "node_name": "generate",
                "code": "GENERATE_LLM_PARSE_FALLBACK",
                "message": f"Generation LLM output could not be parsed; fallback stories were generated. Error: {type(e).__name__}: {str(e)}"
            }
        ]

        return {
            **result,
            "warnings": existing_warnings + new_warnings,
            "status": "partial"
        }
