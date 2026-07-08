import json
import logging
import asyncio
from typing import List, Dict, Any, Tuple
from pydantic import BaseModel, Field, ValidationError

from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion, QualityIssue, PipelineWarning
from app.config import settings
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from app.progress import update_progress

logger = logging.getLogger("app.nodes.repair_stories")

REPAIRABLE_RULES = {
    "story_missing_acceptance",
    "story_description_shape",
    "story_empty_title",
    "generic_acceptance_criteria",
    "insufficient_acceptance_criteria",
    "all_generic_acceptance_criteria",
    "weak_description",
    "missing_title",
}


class RepairedStoryItem(BaseModel):
    id: str
    title: str
    description: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)


class RepairedStoriesResponse(BaseModel):
    stories: List[RepairedStoryItem] = Field(default_factory=list)


def _build_repair_prompt_context(
    story: UserStory,
    story_issues: List[QualityIssue],
    req_map: Dict[int, Any]
) -> str:
    issues_text = "\n".join([f"- {issue.rule_violated}: {issue.details}" for issue in story_issues])
    
    req_texts = []
    for r_id in story.source_requirement_ids:
        req = req_map.get(r_id)
        if req:
            req_texts.append(f"Requirement {r_id}: {req.text} (Actor: {req.actor or 'System'})")
    reqs_context = "\n".join(req_texts)
    
    current_ac = "\n".join([f"  - AC {idx+1}: {ac.text}" for idx, ac in enumerate(story.acceptance_criteria)])

    return (
        f"--- Failed Story ID: {story.id} ---\n"
        f"Title: {story.title}\n"
        f"Description: {story.description}\n"
        f"Current Acceptance Criteria:\n{current_ac}\n"
        f"Quality Issues to Fix:\n{issues_text}\n"
        f"Source Requirement Context:\n{reqs_context}\n"
    )


async def repair_stories_node(state: PipelineState) -> dict:
    print("--- QUALITY REPAIR NODE ---")
    update_progress(state.get("job_id"), "quality_repair", 91, "PROCESSING")

    attempts = state.get("repair_attempts", 0)
    stories = state.get("user_stories", []) or []
    issues = state.get("quality_issues", []) or []
    reqs = state.get("classified_requirements", []) or state.get("extracted_requirements", []) or []
    req_map = {r.id: r for r in reqs}

    story_ids = {s.id for s in stories}
    story_map = {s.id: s for s in stories}

    # Group issues by story ID
    story_issues_map: Dict[str, List[QualityIssue]] = {}
    for issue in issues:
        # Pydantic or dict shape safety
        item_type = getattr(issue, "item_type", "") if hasattr(issue, "item_type") else issue.get("item_type", "")
        rule = getattr(issue, "rule_violated", "") if hasattr(issue, "rule_violated") else issue.get("rule_violated", "")
        # Since item_id in QualityIssue is an int (usually 0 for stories or links back to requirement IDs),
        # we check details or check rule_violated to match stories.
        # Let's inspect details to extract the story ID (e.g. "Story job_story_1 ...") or check how quality_gate builds them:
        # In quality_gate.py: new_issues.append(QualityIssue(item_id=0, item_type="story", rule_violated="...", details=f"Story {s.id} ..."))
        # We can extract the story ID from the details string if item_id is 0 or not matching story.id directly.
        # Let's write a robust story ID parser from details if item_type == "story".
        if item_type == "story":
            details = getattr(issue, "details", "") if hasattr(issue, "details") else issue.get("details", "")
            # Find the story ID in details, which has format "... story <job_id_story_X> ..."
            # We can search for any word in details that matches story_ids
            found_story_id = None
            for s_id in story_ids:
                if s_id in details:
                    found_story_id = s_id
                    break
            
            if found_story_id and rule in REPAIRABLE_RULES:
                story_issues_map.setdefault(found_story_id, []).append(issue)

    # Filter stories that have repairable issues
    failed_stories = [story_map[s_id] for s_id in story_issues_map if s_id in story_map]

    if not failed_stories:
        logger.info("No repairable story issues found. Skipping repair pass.")
        return {
            "repair_attempts": attempts + 1
        }

    llm = get_llm()
    if llm is None:
        logger.warning("LLM reasoning service not initialized. Skipping quality repair pass.")
        return {
            "repair_attempts": attempts + 1
        }

    try:
        system_prompt = load_prompt(PromptId.REPAIR_STORIES_V1)
        
        # Build batched stories context
        stories_contexts = []
        for s in failed_stories:
            s_issues = story_issues_map[s.id]
            stories_contexts.append(_build_repair_prompt_context(s, s_issues, req_map))
        
        user_prompt = "\n".join(stories_contexts)

        timeout = getattr(settings, "PROVIDER_TIMEOUT_SECONDS", 120)
        raw = await asyncio.wait_for(
            llm.ainvoke([
                ("system", system_prompt),
                ("user", user_prompt)
            ]),
            timeout=float(timeout)
        )
        content = getattr(raw, "content", None) or str(raw)

        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        parsed = json.loads(content)
        response = RepairedStoriesResponse.model_validate(parsed)
        
        repaired_map = {item.id: item for item in response.stories}

        # Reconstruct story list
        updated_stories = []
        repaired_ids = set()
        for s in stories:
            if s.id in repaired_map:
                repaired_item = repaired_map[s.id]
                # Reconstruct AcceptanceCriterion objects
                ac_list = [
                    AcceptanceCriterion(
                        id=f"{s.id}_ac_{idx+1}",
                        text=ac,
                        criterion_type="Given-When-Then" if "Given" in ac else "plain"
                    )
                    for idx, ac in enumerate(repaired_item.acceptance_criteria)
                ]
                
                # Create the repaired UserStory preserving other original metadata
                updated_story = UserStory(
                    id=s.id,
                    title=repaired_item.title or s.title,
                    description=repaired_item.description or s.description,
                    acceptance_criteria=ac_list,
                    source_requirement_ids=s.source_requirement_ids,
                    labels=repaired_item.labels or s.labels,
                    priority=s.priority,
                    evidence_reference=s.evidence_reference,
                    source_fr_id=s.source_fr_id
                )
                updated_stories.append(updated_story)
                repaired_ids.add(s.id)
            else:
                # Good stories are kept exactly identical (same object reference)
                updated_stories.append(s)

        # Track resolved issues
        resolved_issues = []
        remaining_issues = []
        for issue in issues:
            item_type = getattr(issue, "item_type", "") if hasattr(issue, "item_type") else issue.get("item_type", "")
            rule = getattr(issue, "rule_violated", "") if hasattr(issue, "rule_violated") else issue.get("rule_violated", "")
            details = getattr(issue, "details", "") if hasattr(issue, "details") else issue.get("details", "")
            
            # Find if this issue belongs to a successfully repaired story
            is_resolved = False
            if item_type == "story" and rule in REPAIRABLE_RULES:
                for r_id in repaired_ids:
                    if r_id in details:
                        is_resolved = True
                        break
            
            if is_resolved:
                resolved_issues.append(issue)
            else:
                remaining_issues.append(issue)

        logger.info("Successfully repaired %d user stories during quality repair pass.", len(repaired_ids))
        
        return {
            "user_stories": updated_stories,
            "quality_issues": remaining_issues,
            "resolved_quality_issues": (state.get("resolved_quality_issues", []) or []) + resolved_issues,
            "repair_attempts": attempts + 1
        }

    except Exception as e:
        logger.warning("Failed to execute quality repair pass: %s. Keeping stories unchanged.", e, exc_info=True)
        # Fallback: keep stories unchanged but still increment attempts to prevent infinite loop
        return {
            "repair_attempts": attempts + 1
        }
