from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion, RequirementCoverage
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from pydantic import BaseModel, Field
from typing import List, Any, Optional
import json
import re
import traceback


# ---------------- PROMPT ----------------

USER_PROMPT = """
Convert these classified requirements into user stories:

{items}
"""


# ---------------- STRUCTURED OUTPUT ----------------

class StoryResponse(BaseModel):
    source_requirement_id: int
    title: str
    description: str
    acceptance_criteria: List[str]
    labels: List[str]


class GenerationResponse(BaseModel):
    stories: List[StoryResponse] = Field(description="Generated user stories")


# ---------------- HELPERS ----------------

def normalize_actor_to_agile_role(actor: Optional[str]) -> str:
    if not actor or str(actor).lower() == "none":
        return "a user"
    
    actor_lower = str(actor).lower().strip()
    
    # 1. Handle common group/plural patterns
    mapping = {
        "warehouse staff": "a warehouse staff member",
        "staff": "a staff member",
        "employees": "an employee",
        "managers": "a manager",
        "admins": "an admin",
        "sales representatives": "a sales representative",
        "sales reps": "a sales representative",
        "users": "a user",
        "user": "a user",
        "customers": "a customer",
        "guests": "a guest",
        "stakeholders": "a stakeholder",
    }
    
    if actor_lower in mapping:
        return mapping[actor_lower]
    
    # 2. Heuristic for "an" vs "a"
    # Note: "user" starts with a vowel but sounds like 'y' (consonant)
    vowels = ("a", "e", "i", "o", "u")
    if actor_lower.startswith(vowels) and not actor_lower.startswith("user"):
        if not actor_lower.startswith(("a ", "an ", "the ")):
            return f"an {actor_lower}"
    else:
        if not actor_lower.startswith(("a ", "an ", "the ")):
            return f"a {actor_lower}"
            
    return actor_lower

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
    return labels or []


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
    special_non_story_labels = {"Open Question", "Out-of-Scope", "Assumption"}

    for req in classified:
        labels = set(_normalize_labels(getattr(req, "labels", None)))
        candidate_labels = set(getattr(req, "candidate_labels", []) or [])
        
        # If any label (final or candidate) is in the special set, skip generation
        if (labels | candidate_labels) & special_non_story_labels:
            to_skip.append(req)
        else:
            to_generate.append(req)

    requirement_coverages = []
    for req in to_skip:
        coverage = RequirementCoverage(
            requirement_id=req.id,
            coverage_type="non_story",
            story_ids=[],
            acceptance_criteria_ids=[],
            reason="Open questions, assumptions, and out-of-scope items are not converted into user stories."
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

        system_prompt = load_prompt(PromptId.GENERATE_USER_STORIES_V1)
        items_text = "\n\n".join(_format_requirement(req) for req in to_generate)

        raw = await llm.ainvoke([
            ("system", system_prompt),
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

        llm_stories = response.stories if response else []

        # Create lookup map for LLM output, handling duplicates by keeping first
        llm_story_map = {}
        for s in llm_stories:
            if s.source_requirement_id not in llm_story_map:
                llm_story_map[s.source_requirement_id] = s

        final_stories = []
        job_id = state.get("job_id") or "job"

        for req in to_generate:
            story_id = f"{job_id}_story_{req.id}"
            
            # Match LLM output or use fallback
            llm_s = llm_story_map.get(req.id)
            
            if llm_s:
                # 1. Normal Path (Matched LLM Output)
                # Normalize role in description if needed
                desc = llm_s.description
                if desc.startswith("As "):
                    match = re.match(r"^As\s+(.+?),\s+I\s+(want|must)", desc, re.IGNORECASE)
                    if match:
                        original_role = match.group(1)
                        stripped_role = re.sub(r"^(a|an|the)\s+", "", original_role, flags=re.IGNORECASE)
                        normalized_role = normalize_actor_to_agile_role(stripped_role)
                        desc = desc.replace(original_role, normalized_role, 1)

                ac_list = []
                for i, c in enumerate(llm_s.acceptance_criteria):
                    ac_list.append(
                        AcceptanceCriterion(
                            id=f"{story_id}_ac_{i+1}",
                            text=c,
                            criterion_type="Given-When-Then" if "Given" in c else "plain"
                        )
                    )

                user_story = UserStory(
                    id=story_id,
                    title=llm_s.title,
                    description=desc,
                    acceptance_criteria=ac_list,
                    source_requirement_ids=[req.id],
                    labels=_normalize_labels(getattr(llm_s, "labels", ["FR"])),
                    evidence_reference=getattr(req, "evidence", [])
                )
            else:
                # 2. Fallback Path (LLM skipped this requirement)
                actor = getattr(req, "actor", None)
                goal = getattr(req, "goal", None)
                
                if not actor:
                    req_text_lower = req.text.lower()
                    if "admin" in req_text_lower: actor = "admin"
                    elif "sales representative" in req_text_lower: actor = "sales representative"
                    elif "viewer" in req_text_lower: actor = "viewer"
                    elif set(_normalize_labels(getattr(req, "labels", None))).issubset({"NFR", "BR"}): actor = "system"
                    else: actor = "user"
                
                if not goal: goal = "satisfy this requirement"

                agile_actor = normalize_actor_to_agile_role(actor)
                
                user_story = UserStory(
                    id=story_id,
                    title=f"Story for requirement {req.id}",
                    description=f"As {agile_actor}, I want {goal}, so that: {req.text}",
                    acceptance_criteria=[
                        AcceptanceCriterion(
                            id=f"{story_id}_ac_1",
                            text="Requirement is implemented as specified",
                            criterion_type="plain"
                        )
                    ],
                    source_requirement_ids=[req.id],
                    labels=_normalize_labels(getattr(req, "labels", None)),
                    evidence_reference=getattr(req, "evidence", [])
                )

            final_stories.append(user_story)
            
            # create coverage record for this requirement
            coverage = RequirementCoverage(
                requirement_id=req.id,
                coverage_type="covered_by_story",
                story_ids=[story_id],
                acceptance_criteria_ids=[c.id for c in user_story.acceptance_criteria],
                reason=None
            )
            requirement_coverages.append(coverage)
            
        return {"user_stories": final_stories, "requirement_coverages": requirement_coverages}

    except Exception as e:
        print(f"Generate node LLM failure or parse error: {e}")
        traceback.print_exc()

        # ---------------- TOTAL FALLBACK ----------------
        fallback_stories = []
        job_id = state.get("job_id") or "job"

        for req in to_generate:
            story_id = f"{job_id}_story_{req.id}"
            actor = getattr(req, "actor", None)
            goal = getattr(req, "goal", None)
            
            if not actor:
                req_text_lower = req.text.lower()
                if "admin" in req_text_lower: actor = "admin"
                elif "sales representative" in req_text_lower: actor = "sales representative"
                elif "viewer" in req_text_lower: actor = "viewer"
                elif set(_normalize_labels(getattr(req, "labels", None))).issubset({"NFR", "BR"}): actor = "system"
                else: actor = "user"
            
            if not goal: goal = "satisfy this requirement"

            agile_actor = normalize_actor_to_agile_role(actor)

            user_story = UserStory(
                id=story_id,
                title=f"Story for requirement {req.id}",
                description=f"As {agile_actor}, I want {goal}, so that: {req.text}",
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id=f"{story_id}_ac_1",
                        text="Requirement is implemented as specified",
                        criterion_type="plain"
                    )
                ],
                source_requirement_ids=[req.id],
                labels=_normalize_labels(getattr(req, "labels", None)),
                evidence_reference=getattr(req, "evidence", [])
            )
            fallback_stories.append(user_story)

            coverage = RequirementCoverage(
                requirement_id=req.id,
                coverage_type="covered_by_story",
                story_ids=[story_id],
                acceptance_criteria_ids=[c.id for c in user_story.acceptance_criteria],
                reason=None
            )
            requirement_coverages.append(coverage)

        existing_warnings = state.get("warnings", []) or []
        new_warnings = [{
            "node_name": "generate",
            "code": "GENERATE_LLM_FAILURE_FALLBACK",
            "message": f"Generation LLM failed or output could not be parsed; fallback stories generated. Error: {type(e).__name__}: {str(e)}"
        }]

        return {
            "user_stories": fallback_stories,
            "requirement_coverages": requirement_coverages,
            "warnings": existing_warnings + new_warnings,
            "status": "partial"
        }
