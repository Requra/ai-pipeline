from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion, RequirementCoverage
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from app.progress import update_progress
from app.nodes.dedupe_requirements import canonicalize_requirements
from app.nodes.extract import project_legacy_requirements
from app.validators.story_validator import find_duplicate_story_ids, validate_stories
from app.services.semantic_quality import (
    clause_coverage,
    fact_tokens,
    has_polarity_conflict,
    is_substantive,
    MIN_STORY_ALIGNMENT,
    proposition_support,
    normalize_story_points,
    source_fact_texts,
    split_requirement_clauses,
    story_alignment,
    unsupported_fact_terms,
    unsupported_numeric_claims,
)
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
    source_requirement_ids: Optional[List[int]] = Field(default=None, description="IDs of source requirements mapping to this story. Can list multiple IDs to merge related requirements.")
    source_requirement_id: Optional[int] = Field(default=None, description="Legacy single ID field.")
    title: str
    description: str
    acceptance_criteria: List[str]
    labels: List[str]
    story_points: Optional[int] = Field(default=0, description="Fibonacci estimate (1, 2, 3, 5, 8) based on complexity.")


class GenerationResponse(BaseModel):
    stories: List[StoryResponse] = Field(description="Generated user stories")


# ---------------- HELPERS ----------------

def normalize_actor_to_agile_role(actor: Optional[str]) -> str:
    if not actor or str(actor).lower() == "none":
        return "a user"
    
    actor_lower = str(actor).lower().strip()

    if actor_lower in {
        "system", "service", "application", "portal", "workspace",
        "notification service", "export service", "reporting service",
    }:
        return "a system operator"
    
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


def _primary_label(req) -> str:
    """Pick the dominant label for AC shaping (FR > BR > NFR by default)."""
    labels = set(_normalize_labels(getattr(req, "labels", None))) | set(getattr(req, "candidate_labels", []) or [])
    if "FR" in labels:
        return "FR"
    if "BR" in labels:
        return "BR"
    if "NFR" in labels or "Constraint" in labels:
        return "NFR"
    return "FR"


def build_specific_acceptance_criteria(req, story_id: str, agile_actor: str) -> List[AcceptanceCriterion]:
    """Deterministic, requirement-specific acceptance criteria for fallbacks.

    Embeds the requirement text so the criteria are concrete and testable rather
    than generic boilerplate. Always returns at least two Given-When-Then items.
    """
    return build_source_bound_acceptance_criteria([req], story_id)


def build_source_bound_acceptance_criteria(requirements, story_id: str) -> List[AcceptanceCriterion]:
    """Create criteria that restate only facts present in canonical requirements."""
    clauses = [
        clause
        for req in requirements
        for clause in split_requirement_clauses(getattr(req, "text", "") or "")
    ]
    if not clauses:
        clauses = ["the linked source requirement"]

    criteria = [
        f"Given the documented preconditions apply, when the capability is exercised, then {clause.rstrip('.')} ."
        for clause in clauses
    ]
    if len(criteria) == 1:
        clause = clauses[0].rstrip(".")
        criteria.append(
            f"Given the capability has been exercised, when its result is evaluated, then the observed outcome conforms to: {clause}."
        )
    return [
        AcceptanceCriterion(
            id=f"{story_id}_ac_{index + 1}",
            text=text.replace(" .", "."),
            criterion_type="Given-When-Then",
        )
        for index, text in enumerate(criteria)
    ]


def _criterion_supported(text: str, requirements) -> bool:
    sources = source_fact_texts(requirements)
    if not sources:
        return False
    if unsupported_numeric_claims(text, sources):
        return False
    if unsupported_fact_terms(text, sources):
        return False
    if has_polarity_conflict(text, sources):
        return False
    return max(
        (proposition_support(source, text) for source in sources),
        default=0.0,
    ) >= 0.15


def _mapping_supported(requirement, story_text: str) -> bool:
    req_text = getattr(requirement, "text", "") or ""
    if (
        not is_substantive(req_text)
        or story_alignment([req_text], story_text) >= MIN_STORY_ALIGNMENT
    ):
        return True
    req_tokens = fact_tokens(req_text)
    story_tokens = fact_tokens(story_text)
    return bool(req_tokens) and len(req_tokens & story_tokens) / len(req_tokens) >= 0.25


def _sanitize_generated_story(story: UserStory, req_map: dict[int, Any]) -> UserStory:
    """Remove unsupported mappings/facts and constrain estimates before output."""
    story_text = " ".join([
        story.title,
        story.description,
        *[
            getattr(criterion, "text", "") or ""
            for criterion in (story.acceptance_criteria or [])
        ],
    ])
    valid_ids = []
    for req_id in dict.fromkeys(story.source_requirement_ids):
        req = req_map.get(req_id)
        if req is None:
            continue
        if _mapping_supported(req, story_text):
            valid_ids.append(req_id)
    if not valid_ids:
        valid_ids = [req_id for req_id in story.source_requirement_ids if req_id in req_map][:1]
    story.source_requirement_ids = valid_ids

    linked = [req_map[req_id] for req_id in valid_ids if req_id in req_map]
    linked_labels = [
        label
        for req in linked
        for label in _normalize_labels(getattr(req, "labels", None))
    ]
    if linked_labels:
        story.labels = list(dict.fromkeys(linked_labels))
    linked_priorities = [getattr(req, "priority", "Medium") for req in linked]
    story.priority = max(
        linked_priorities or ["Medium"],
        key=lambda value: {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}.get(value, 1),
    )
    supported_criteria = [
        criterion for criterion in story.acceptance_criteria
        if _criterion_supported(criterion.text, linked)
    ]
    criterion_texts = [criterion.text for criterion in supported_criteria]
    if len(supported_criteria) < 2 or clause_coverage(linked, criterion_texts) < 1.0:
        story.acceptance_criteria = build_source_bound_acceptance_criteria(linked, story.id)
    else:
        for index, criterion in enumerate(supported_criteria, start=1):
            criterion.id = f"{story.id}_ac_{index}"
            criterion.criterion_type = "Given-When-Then"
        story.acceptance_criteria = supported_criteria

    story.story_points = normalize_story_points(
        story.story_points,
        [getattr(req, "text", "") or "" for req in linked],
    )
    story.evidence_reference = [
        evidence for req in linked for evidence in (getattr(req, "evidence", []) or [])
    ]
    return story


def _dedupe_generated_stories(stories: List[UserStory]) -> List[UserStory]:
    """Merge duplicate generated propositions while preserving coverage."""
    canonical: List[UserStory] = []
    for story in stories:
        duplicate = None
        story_tokens = set(re.findall(r"[a-z0-9]+", story.description.lower()))
        for existing in canonical:
            existing_tokens = set(re.findall(r"[a-z0-9]+", existing.description.lower()))
            union = story_tokens | existing_tokens
            similarity = len(story_tokens & existing_tokens) / len(union) if union else 0.0
            if (
                story.title.strip().lower() == existing.title.strip().lower()
                or similarity >= 0.90
            ):
                duplicate = existing
                break
        if duplicate is None:
            canonical.append(story)
            continue
        duplicate.source_requirement_ids = list(dict.fromkeys(
            duplicate.source_requirement_ids + story.source_requirement_ids
        ))
        duplicate.labels = list(dict.fromkeys(duplicate.labels + story.labels))
        duplicate.evidence_reference.extend(
            evidence for evidence in story.evidence_reference
            if (evidence.chunk_id, evidence.quote) not in {
                (current.chunk_id, current.quote) for current in duplicate.evidence_reference
            }
        )
        duplicate.priority = max(
            (duplicate.priority, story.priority),
            key=lambda value: {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}.get(value, 1),
        )
        duplicate.story_points = max(duplicate.story_points, story.story_points)
    return canonical


# ---------------- NODE ----------------


def _remap_pre_generation_issues(issues, id_map: dict[int, int], requirements) -> list:
    """Reconcile requirement issues after the final canonicalization pass."""
    requirements_by_id = {req.id: req for req in requirements}
    evidence_rules = {
        "missing_evidence",
        "missing_verified_evidence",
        "evidence_semantic_mismatch",
    }
    duplicate_rules = {
        "duplicate_requirement",
        "semantic_conflict_duplicate",
    }
    remapped = []
    seen = set()
    for issue in issues or []:
        item_type = getattr(issue, "item_type", "")
        old_item_id = getattr(issue, "item_id", None)
        new_item_id = id_map.get(old_item_id, old_item_id)
        rule = getattr(issue, "rule_violated", "")
        if rule in duplicate_rules:
            continue
        linked = requirements_by_id.get(new_item_id)
        if rule in evidence_rules and linked is not None and linked.evidence:
            continue
        updated = (
            issue.model_copy(update={"item_id": new_item_id})
            if item_type in {"requirement", "coverage"} and new_item_id != old_item_id
            else issue
        )
        key = (
            getattr(updated, "item_type", ""),
            getattr(updated, "item_id", None),
            getattr(updated, "rule_violated", ""),
            getattr(updated, "details", ""),
        )
        if key not in seen:
            seen.add(key)
            remapped.append(updated)
    return remapped


async def generate_node(state: PipelineState) -> dict:
    print("--- GENERATE NODE (MULTI-LABEL) ---")
    update_progress(state.get("job_id"), "generate", 85, "PROCESSING")

    classified = list(state.get("classified_requirements", []) or [])
    if not classified:
        new_warnings = [
            {"node_name": "generate", "code": "GENERATE_SKIPPED_NO_REQUIREMENTS", "message": "No classified requirements available; generation skipped."}
        ]
        existing_warnings = state.get("warnings", []) or []
        return {"user_stories": [], "warnings": existing_warnings + new_warnings}

    classified, merged_count, id_map, _ = canonicalize_requirements(
        classified,
        reassign_ids=False,
    )
    canonical_changed = merged_count > 0 or any(
        old_id != new_id for old_id, new_id in id_map.items()
    )
    canonical_warning = None
    canonical_issues = state.get("quality_issues", []) or []
    if canonical_changed:
        canonical_issues = _remap_pre_generation_issues(
            canonical_issues,
            id_map,
            classified,
        )
    if merged_count:
        canonical_warning = {
            "node_name": "generate",
            "code": "PRE_GENERATION_DUPLICATE_MERGED",
            "message": (
                f"Merged {merged_count} duplicate requirement(s) during the "
                "final pre-generation canonicalization pass."
            ),
        }

    def finalize(payload: dict) -> dict:
        payload["classified_requirements"] = classified
        payload["functional_requirements"] = project_legacy_requirements(classified)
        if canonical_changed:
            payload["quality_issues"] = canonical_issues
        if merged_count:
            warnings = list(payload.get("warnings", state.get("warnings", []) or []))
            if not any(
                getattr(warning, "code", None) == "PRE_GENERATION_DUPLICATE_MERGED"
                or (
                    isinstance(warning, dict)
                    and warning.get("code") == "PRE_GENERATION_DUPLICATE_MERGED"
                )
                for warning in warnings
            ):
                warnings.append(canonical_warning)
            payload["warnings"] = warnings
        return payload

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
        return finalize({
            "user_stories": [], 
            "warnings": existing_warnings + new_warnings,
            "requirement_coverages": requirement_coverages
        })


    try:
        llm = get_llm()

        if llm is None:
            raise RuntimeError("LLM not initialized")

        system_prompt = load_prompt(PromptId.GENERATE_USER_STORIES_V2)
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

        req_map = {r.id: r for r in to_generate}

        # Create a lookup only for source IDs whose proposition actually aligns
        # with the generated story. Declared IDs are not trusted by themselves.
        llm_story_map = {}
        for s in llm_stories:
            req_ids = []
            if s.source_requirement_ids:
                req_ids.extend(s.source_requirement_ids)
            if s.source_requirement_id is not None:
                req_ids.append(s.source_requirement_id)
            story_text = " ".join([
                s.title,
                s.description,
                *[
                    getattr(criterion, "text", str(criterion)) or ""
                    for criterion in (s.acceptance_criteria or [])
                ],
            ])
            for r_id in dict.fromkeys(req_ids):
                req = req_map.get(r_id)
                if req is None:
                    continue
                aligned = _mapping_supported(req, story_text)
                if aligned and r_id not in llm_story_map:
                    llm_story_map[r_id] = s

        final_stories = []
        job_id = state.get("job_id") or "job"
        
        # Track created UserStory instances by their corresponding LLM StoryResponse object ID
        created_stories = {} # id(StoryResponse) -> UserStory

        for req in to_generate:
            llm_s = llm_story_map.get(req.id)
            
            if llm_s:
                # If we've already created a story for this exact LLM StoryResponse (due to N:1 mapping),
                # append this requirement's ID and labels to it, and map coverage.
                llm_s_id = id(llm_s)
                if llm_s_id in created_stories:
                    user_story = created_stories[llm_s_id]
                    if req.id not in user_story.source_requirement_ids:
                        user_story.source_requirement_ids.append(req.id)
                    # Accumulate labels
                    for label in _normalize_labels(getattr(llm_s, "labels", ["FR"])):
                        if label not in user_story.labels:
                            user_story.labels.append(label)
                    
                    # Recompute priority with the new requirement included
                    req_priorities = [
                        getattr(req_map[r_id], "priority", "Medium")
                        for r_id in user_story.source_requirement_ids
                        if r_id in req_map
                    ]
                    if "Critical" in req_priorities:
                        user_story.priority = "Critical"
                    elif "High" in req_priorities:
                        user_story.priority = "High"
                    elif "Medium" in req_priorities:
                        user_story.priority = "Medium"
                    elif "Low" in req_priorities and all(p == "Low" for p in req_priorities):
                        user_story.priority = "Low"
                    
                    # Update coverage record
                    coverage = RequirementCoverage(
                        requirement_id=req.id,
                        coverage_type="merged_into_story",
                        story_ids=[user_story.id],
                        acceptance_criteria_ids=[c.id for c in user_story.acceptance_criteria],
                        reason=None
                    )
                    requirement_coverages.append(coverage)
                    continue

                # 1. Normal Path (First time seeing this StoryResponse)
                story_id = f"{job_id}_story_{req.id}"
                
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

                # Initialize list of source requirements for this story
                story_req_ids = []
                if llm_s.source_requirement_ids:
                    story_req_ids.extend(llm_s.source_requirement_ids)
                if llm_s.source_requirement_id is not None:
                    story_req_ids.append(llm_s.source_requirement_id)
                # Keep it unique and ensure the current requirement's ID is in the list
                story_req_ids = list(dict.fromkeys(story_req_ids))
                if req.id not in story_req_ids:
                    story_req_ids.append(req.id)

                # Determine story priority based on highest priority of source requirements
                story_priority = "Medium"
                req_priorities = [
                    getattr(req_map[r_id], "priority", "Medium")
                    for r_id in story_req_ids
                    if r_id in req_map
                ]
                if "Critical" in req_priorities:
                    story_priority = "Critical"
                elif "High" in req_priorities:
                    story_priority = "High"
                elif "Medium" in req_priorities:
                    story_priority = "Medium"
                elif "Low" in req_priorities and all(p == "Low" for p in req_priorities):
                    story_priority = "Low"

                user_story = UserStory(
                    id=story_id,
                    title=llm_s.title,
                    description=desc,
                    acceptance_criteria=ac_list,
                    source_requirement_ids=story_req_ids,
                    labels=_normalize_labels(getattr(llm_s, "labels", ["FR"])),
                    priority=story_priority,
                    evidence_reference=getattr(req, "evidence", []),
                    story_points=normalize_story_points(
                        getattr(llm_s, "story_points", 0),
                        [getattr(req_map[r_id], "text", "") for r_id in story_req_ids if r_id in req_map],
                    )
                )
                created_stories[llm_s_id] = user_story
                final_stories.append(user_story)
                
                # Determine coverage type based on number of mapped requirement IDs
                coverage_type = "merged_into_story" if len(story_req_ids) > 1 else "covered_by_story"
                
                coverage = RequirementCoverage(
                    requirement_id=req.id,
                    coverage_type=coverage_type,
                    story_ids=[story_id],
                    acceptance_criteria_ids=[c.id for c in user_story.acceptance_criteria],
                    reason=None
                )
                requirement_coverages.append(coverage)
            else:
                # 2. Fallback Path (LLM skipped this requirement)
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
                    acceptance_criteria=build_specific_acceptance_criteria(req, story_id, agile_actor),
                    source_requirement_ids=[req.id],
                    labels=_normalize_labels(getattr(req, "labels", None)),
                    priority=getattr(req, "priority", "Medium"),
                    evidence_reference=getattr(req, "evidence", []),
                    story_points=normalize_story_points(0, [getattr(req, "text", "") or ""]),
                )
                final_stories.append(user_story)
                
                coverage = RequirementCoverage(
                    requirement_id=req.id,
                    coverage_type="covered_by_story",
                    story_ids=[story_id],
                    acceptance_criteria_ids=[c.id for c in user_story.acceptance_criteria],
                    reason=None
                )
                requirement_coverages.append(coverage)
            
        final_stories = [
            _sanitize_generated_story(story, req_map) for story in final_stories
        ]
        final_stories = _dedupe_generated_stories(final_stories)

        # Rebuild actionable coverage after mapping validation and story
        # deduplication so it can never point to a removed or unrelated story.
        requirement_coverages = [coverage for coverage in requirement_coverages if coverage.coverage_type == "non_story"]
        for req in to_generate:
            linked_story = next(
                (story for story in final_stories if req.id in story.source_requirement_ids),
                None,
            )
            if linked_story is None:
                requirement_coverages.append(RequirementCoverage(
                    requirement_id=req.id,
                    coverage_type="needs_review",
                    story_ids=[],
                    acceptance_criteria_ids=[],
                    reason="No semantically aligned generated story remained after validation.",
                ))
            else:
                requirement_coverages.append(RequirementCoverage(
                    requirement_id=req.id,
                    coverage_type=(
                        "merged_into_story" if len(linked_story.source_requirement_ids) > 1
                        else "covered_by_story"
                    ),
                    story_ids=[linked_story.id],
                    acceptance_criteria_ids=[criterion.id for criterion in linked_story.acceptance_criteria],
                    reason=None,
                ))

        # Validate generated stories and surface an aggregate quality warning.
        # We flag (not mutate) LLM stories so coverage stays consistent; the
        # fallback path already emits >=2 specific criteria.
        result_payload: dict = {
            "user_stories": final_stories,
            "requirement_coverages": requirement_coverages,
        }
        reqs_by_id = {r.id: r for r in classified}
        issues_by_story = validate_stories(final_stories, reqs_by_id)
        duplicate_ids = find_duplicate_story_ids(final_stories)
        if issues_by_story or duplicate_ids:
            codes = sorted({code for codes in issues_by_story.values() for code in codes})
            parts = []
            if issues_by_story:
                parts.append(f"{len(issues_by_story)} story(ies) with issues ({', '.join(codes)})")
            if duplicate_ids:
                parts.append(f"{len(duplicate_ids)} duplicate story(ies)")
            existing_warnings = state.get("warnings", []) or []
            result_payload["warnings"] = existing_warnings + [{
                "node_name": "generate",
                "code": "GENERATE_STORY_QUALITY",
                "message": "Generated story quality issues: " + "; ".join(parts) + ".",
            }]
        return finalize(result_payload)

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
                acceptance_criteria=build_specific_acceptance_criteria(req, story_id, agile_actor),
                source_requirement_ids=[req.id],
                labels=_normalize_labels(getattr(req, "labels", None)),
                priority=getattr(req, "priority", "Medium"),
                evidence_reference=getattr(req, "evidence", []),
                story_points=normalize_story_points(0, [getattr(req, "text", "") or ""]),
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

        return finalize({
            "user_stories": fallback_stories,
            "requirement_coverages": requirement_coverages,
            "warnings": existing_warnings + new_warnings,
            "status": "partial"
        })
