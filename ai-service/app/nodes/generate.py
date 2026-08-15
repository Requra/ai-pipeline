from app.schemas.pipeline_state import PipelineState
from app.schemas.items import UserStory, AcceptanceCriterion, RequirementCoverage, QualityIssue
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from app.progress import update_progress
from app.nodes.dedupe_requirements import canonicalize_requirements
from app.nodes.extract import project_legacy_requirements
from app.validators.story_validator import (
    find_duplicate_acceptance_criterion_ids,
    find_duplicate_story_ids,
    validate_stories,
)
from app.services.semantic_quality import (
    clause_coverage,
    fact_tokens,
    has_polarity_conflict,
    introduces_unsupported_approval_outcome,
    is_substantive,
    MIN_STORY_ALIGNMENT,
    proposition_support,
    normalize_story_points,
    numeric_upper_bound_entails,
    source_fact_texts,
    split_requirement_clauses,
    story_alignment,
    unsupported_fact_terms,
    unsupported_numeric_claims,
    unsupported_review_terms,
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


def normalize_story_persona(description: str) -> str:
    """Replace technical personas and safe casing artifacts in a story."""
    text = (description or "").strip()
    technical_persona = re.compile(
        r"^As\s+(?:a|an|the)\s+"
        r"(?:system|service|application|portal|workspace|database|api)\s*,",
        re.IGNORECASE,
    )
    if technical_persona.search(text):
        text = technical_persona.sub("As a system operator,", text, count=1)
    # Some providers capitalize the first verb after ``I want to``. This is a
    # grammar-only normalization and preserves the stated behavior.
    return re.sub(
        r"(\bI\s+want\s+to\s+)([A-Z])",
        lambda match: match.group(1) + match.group(2).lower(),
        text,
    )

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
    than generic boilerplate. Atomic requirements may correctly produce one
    criterion; composite requirements produce one per independent clause.
    """
    return build_source_bound_acceptance_criteria([req], story_id)


def _observable_outcome(clause: str) -> str:
    """Convert common requirement modals into readable observable outcomes."""
    text = (clause or "").strip().rstrip(".")
    modal = re.match(
        r"^(?P<subject>.+?)\s+(?:shall|must|will|should)\s+"
        r"(?P<negative>not\s+)?(?P<be>be\s+)?(?P<predicate>.+)$",
        text,
        re.IGNORECASE,
    )
    if not modal:
        return text[:1].lower() + text[1:] if text else text

    subject = modal.group("subject").strip()
    predicate = modal.group("predicate").strip()
    negative = bool(modal.group("negative"))
    is_plural = (
        subject.lower() == "they"
        or subject.lower() in {"users", "records", "assets", "requests", "codes"}
        or subject.lower().endswith((" users", " records", " assets", " requests", " codes"))
    )
    if modal.group("be"):
        verb = "are" if is_plural else "is"
        outcome = f"{subject} {verb}{' not' if negative else ''} {predicate}"
    else:
        words = predicate.split(maxsplit=1)
        action = words[0]
        remainder = f" {words[1]}" if len(words) > 1 else ""
        if not is_plural and not negative:
            def conjugate(verb: str) -> str:
                return verb + (
                    "s" if verb.endswith("e")
                    else "es" if verb.endswith(("s", "x", "z", "ch", "sh"))
                    else "s"
                )

            action = conjugate(action)
            remainder = re.sub(
                r"^(\s+and\s+)([a-z]+)",
                lambda match: match.group(1) + conjugate(match.group(2)),
                remainder,
                count=1,
                flags=re.IGNORECASE,
            )
        auxiliary = "do not " if negative and is_plural else "does not " if negative else ""
        outcome = f"{subject} {auxiliary}{action}{remainder}"
    return outcome[:1].lower() + outcome[1:]


def build_source_bound_acceptance_criteria(requirements, story_id: str) -> List[AcceptanceCriterion]:
    """Create one readable, source-bound criterion per canonical clause."""
    clause_contexts = [
        (req, clause)
        for req in requirements
        for clause in split_requirement_clauses(getattr(req, "text", "") or "")
    ]
    if not clause_contexts:
        clause_contexts = [(None, "The linked source requirement is satisfied")]

    criteria = []
    for req, clause in clause_contexts:
        actor = (getattr(req, "actor", None) or "system").strip() if req else "system"
        goal = (getattr(req, "goal", None) or "the specified behavior").strip() if req else "the specified behavior"
        clause = clause.rstrip(".")
        performer = "the system" if actor.lower() == "system" else actor
        activity = goal.strip().rstrip(".")
        activity = activity[:1].lower() + activity[1:] if activity else "perform the operation"
        activity = re.sub(r"^to\s+", "", activity, flags=re.IGNORECASE)
        outcome = _observable_outcome(clause)
        text = (
            f"Given the required preconditions are satisfied, when {performer} "
            f"attempts to {activity}, then {outcome}."
        )
        criteria.append(text)
        upper_bound = re.search(
            r"\b(?:up to|at most|no more than|maximum(?:\s+of)?|limit(?:ed)?\s+to)\s+"
            r"(?P<number>\d+(?:[,.]\d+)?)\s+(?P<object>[a-z][a-z0-9 _-]{0,40}?)"
            r"(?=\s+(?:simultaneously|at\s+a\s+time)\b|[.,;]|$)",
            clause,
            flags=re.IGNORECASE,
        )
        if upper_bound:
            number = upper_bound.group("number")
            bounded_object = upper_bound.group("object").strip()
            boundary_text = (
                f"Given {performer} already has {number} {bounded_object}, when "
                f"{performer} attempts to {activity} again, then the operation "
                f"does not proceed because the maximum of {number} has been reached."
            )
            if numeric_upper_bound_entails(boundary_text, [clause]):
                criteria.append(boundary_text)
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
    # Do not publish a vague quality claim that is absent from the source. A
    # measurable source constraint (for example, a two-second limit) is not
    # faithfully represented by "in a timely manner".
    lowered = (text or "").lower()
    source_lowered = " ".join(sources).lower()
    vague_outcomes = (
        "in a timely manner", "in a timely fashion", "as quickly as possible",
        "appropriately", "effectively",
    )
    if any(phrase in lowered and phrase not in source_lowered for phrase in vague_outcomes):
        return False
    # A soft-delete rule describes the replacement lifecycle state; it does
    # not by itself authorize an ordinary delete operation. Unless the source
    # explicitly uses deletion as the trigger, reject criteria that introduce
    # "when the record is deleted" as a new precondition.
    source_has_soft_delete = bool(re.search(r"\bsoft[-\s]?delet", source_lowered))
    source_conditions_delete = bool(re.search(
        r"\bwhen\b[^.]{0,80}\bdelet(?:e|ed|ion)\b", source_lowered,
    ))
    candidate_conditions_delete = bool(re.search(
        r"\bwhen\b[^.]{0,80}\b(?:is\s+)?(?:permanently\s+)?deleted\b", lowered,
    ))
    if source_has_soft_delete and candidate_conditions_delete and not source_conditions_delete:
        return False
    if unsupported_numeric_claims(text, sources):
        return False
    if unsupported_fact_terms(text, sources):
        return False
    if unsupported_review_terms(text, sources):
        return False
    if has_polarity_conflict(text, sources):
        return False
    if introduces_unsupported_approval_outcome(text, sources):
        return False
    # A generic criterion can share only "register" and "asset" with a
    # detailed source rule while omitting every required field or constraint.
    # Keep the threshold high enough to force a deterministic source-bound
    # fallback in that case; composite requirements are split into clauses by
    # the fallback builder, so this does not hide independent test cases.
    return max(
        (proposition_support(source, text) for source in sources),
        default=0.0,
    ) >= 0.35


def _linked_audio_evidence(requirements) -> bool:
    """Return whether a story is backed by timestamped transcription evidence.

    This keeps transcript clean-up strictly scoped to audio.  Documents can
    contain timestamps as ordinary text, but they do not populate the internal
    EvidenceSpan timestamp field during parsing.
    """
    return any(
        getattr(evidence, "timestamp", None) is not None
        for requirement in requirements
        for evidence in (getattr(requirement, "evidence", []) or [])
    )


def _linked_human_actor(requirements) -> str | None:
    """Return the first explicit human/business actor from linked requirements."""
    technical = {"system", "service", "application", "portal", "workspace", "database", "api"}
    for requirement in requirements:
        actor = (getattr(requirement, "actor", None) or "").strip()
        if actor and actor.lower() not in technical:
            return normalize_actor_to_agile_role(actor)
    return None


def _normalize_audio_story_wording(text: str) -> str:
    """Correct safe, recurring ASR-to-story grammar artifacts.

    This is intentionally a tiny grammar normalizer, not a semantic rewrite:
    it only changes a malformed inflection to the same source-supported action.
    """
    normalized = text or ""
    normalized = re.sub(r"\bsofts\s+delete\b", "soft-deletes", normalized, flags=re.I)
    normalized = re.sub(r"\bsoft\s+delete\b", "soft-delete", normalized, flags=re.I)
    return normalized


def _audio_unsupported_outcome(text: str, sources) -> bool:
    """Reject a few high-impact outcomes hallucinated from noisy transcripts.

    The general fact validator deliberately treats these as ordinary domain
    nouns for documents.  For audio we can be stricter because a timestamped
    source ledger is present and a model must not turn an absent lifecycle
    state (for example, ``archived``) into a published acceptance outcome.
    """
    source_tokens = fact_tokens(" ".join(sources))
    candidate_tokens = fact_tokens(text)
    return bool((candidate_tokens - source_tokens) & {"archive", "inactive"})


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


def _source_bound_story_wording(requirements) -> tuple[str, str]:
    """Build safe title/description wording from one canonical requirement."""
    req = requirements[0] if requirements else None
    if req is None:
        return "Fulfill documented requirement", (
            "As a user, I want to fulfill the documented requirement, so that "
            "the required outcome is achieved."
        )
    text = (getattr(req, "text", "") or "").strip().rstrip(".")
    sources = source_fact_texts([req])
    goal = (getattr(req, "goal", None) or "").strip().rstrip(".")
    goal_supported = bool(goal) and (
        max((proposition_support(source, goal) for source in sources), default=0.0) >= 0.15
        and not unsupported_numeric_claims(goal, sources)
        and not unsupported_fact_terms(goal, sources)
        and not unsupported_review_terms(goal, sources)
        and not has_polarity_conflict(goal, sources)
    )
    generic_goal = bool(re.match(
        r"^(?:manage|meet|ensure|enable|provide|support|handle)\b",
        goal,
        flags=re.IGNORECASE,
    ))
    if not goal_supported or (generic_goal and len(fact_tokens(goal)) < len(fact_tokens(text))):
        modal = re.match(
            r"^.+?\s+(?:shall|must|will|should|may|can)\s+(?:be\s+able\s+to\s+)?(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        goal = modal.group(1).strip() if modal else text
    goal = goal or "fulfill the documented requirement"
    title = goal[:1].upper() + goal[1:]
    agile_actor = normalize_actor_to_agile_role(getattr(req, "actor", None))
    description = (
        f"As {agile_actor}, I want to {goal[:1].lower() + goal[1:]}, so that "
        "the documented requirement is fulfilled."
    )
    return title[:120], description


def _sanitize_generated_story(story: UserStory, req_map: dict[int, Any]) -> UserStory:
    """Remove unsupported mappings/facts and constrain estimates before output."""
    story.description = normalize_story_persona(story.description)
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
    sources = source_fact_texts(linked)
    human_actor = _linked_human_actor(linked)
    if human_actor and re.match(r"^As\s+a\s+system\s+operator,", story.description, re.I):
        story.description = re.sub(
            r"^As\s+a\s+system\s+operator,",
            f"As {human_actor},",
            story.description,
            count=1,
            flags=re.IGNORECASE,
        )
    if _linked_audio_evidence(linked):
        story.title = _normalize_audio_story_wording(story.title)
        story.description = _normalize_audio_story_wording(story.description)
        for criterion in story.acceptance_criteria or []:
            criterion.text = _normalize_audio_story_wording(criterion.text)
    unsafe_title = (
        unsupported_numeric_claims(story.title, sources)
        or unsupported_fact_terms(story.title, sources)
        or unsupported_review_terms(story.title, sources)
        or has_polarity_conflict(story.title, sources)
        or (_linked_audio_evidence(linked) and _audio_unsupported_outcome(story.title, sources))
    )
    unsafe_description = (
        unsupported_numeric_claims(story.description, sources)
        or unsupported_fact_terms(story.description, sources)
        or unsupported_review_terms(story.description, sources)
        or has_polarity_conflict(story.description, sources)
        or (_linked_audio_evidence(linked) and _audio_unsupported_outcome(story.description, sources))
    )
    generic_fallback_description = "documented requirement is fulfilled" in story.description.lower()
    if unsafe_title or unsafe_description:
        safe_title, safe_description = _source_bound_story_wording(linked)
        if unsafe_title:
            story.title = safe_title
        if unsafe_description:
            story.description = safe_description
    elif generic_fallback_description:
        # The wording is source-safe but not useful to a delivery team. Rebuild
        # it from the linked source proposition rather than exporting boilerplate.
        _safe_title, safe_description = _source_bound_story_wording(linked)
        story.description = safe_description
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
        if (
            _criterion_supported(criterion.text, linked)
            and not (_linked_audio_evidence(linked) and _audio_unsupported_outcome(criterion.text, sources))
        )
    ]
    story.acceptance_criteria = supported_criteria
    duplicate_criterion_ids = set(
        find_duplicate_acceptance_criterion_ids(story, linked)
    )
    supported_criteria = [
        criterion
        for criterion in supported_criteria
        if criterion.id not in duplicate_criterion_ids
    ]
    generated_boundary_criteria = [
        criterion
        for criterion in build_source_bound_acceptance_criteria(linked, story.id)
        if numeric_upper_bound_entails(criterion.text, sources)
    ]
    if generated_boundary_criteria and not any(
        numeric_upper_bound_entails(criterion.text, sources)
        for criterion in supported_criteria
    ):
        # Clause coverage alone cannot prove that an upper boundary is tested:
        # "allow while below N" and "reject N+1" are distinct observable
        # cases. Complete the boundary deterministically from the source rule.
        supported_criteria.extend(generated_boundary_criteria)
    criterion_texts = [criterion.text for criterion in supported_criteria]
    if not supported_criteria or clause_coverage(linked, criterion_texts) < 1.0:
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


def rebuild_requirement_coverages(
    requirements: List[Any],
    stories: List[UserStory],
    existing_coverages: List[RequirementCoverage] | None = None,
) -> List[RequirementCoverage]:
    """Build coverage from the final story objects after generation or repair."""
    existing_coverages = existing_coverages or []
    preserved_non_story = {
        coverage.requirement_id: coverage
        for coverage in existing_coverages
        if coverage.coverage_type == "non_story"
    }
    coverages: List[RequirementCoverage] = []
    for requirement in requirements:
        if requirement.id in preserved_non_story:
            coverages.append(preserved_non_story[requirement.id])
            continue
        linked_story = next(
            (
                story
                for story in stories
                if requirement.id in (story.source_requirement_ids or [])
            ),
            None,
        )
        if linked_story is None:
            coverages.append(RequirementCoverage(
                requirement_id=requirement.id,
                coverage_type="needs_review",
                story_ids=[],
                acceptance_criteria_ids=[],
                reason="No semantically aligned generated story remained after validation.",
            ))
            continue
        coverages.append(RequirementCoverage(
            requirement_id=requirement.id,
            coverage_type=(
                "merged_into_story"
                if len(linked_story.source_requirement_ids) > 1
                else "covered_by_story"
            ),
            story_ids=[linked_story.id],
            acceptance_criteria_ids=[
                criterion.id for criterion in linked_story.acceptance_criteria
            ],
            reason=None,
        ))
    return coverages


def _dedupe_generated_stories(stories: List[UserStory]) -> List[UserStory]:
    """Merge repeated outputs only for the same canonical requirement.

    Similar wording is not sufficient to merge stories for disjoint source
    requirements: the public contract exposes one primary requirement ID and
    such a merge would hide traceability in exports.
    """
    canonical: List[UserStory] = []
    for story in stories:
        duplicate = None
        story_tokens = set(re.findall(r"[a-z0-9]+", story.description.lower()))
        for existing in canonical:
            if not (
                set(story.source_requirement_ids or [])
                & set(existing.source_requirement_ids or [])
            ):
                continue
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
            declared_ids = [r_id for r_id in dict.fromkeys(req_ids) if r_id in req_map]
            # The public story contract has one primary requirement_id. A
            # model-generated N:1 story cannot represent every requirement
            # faithfully in its description and downstream export row.
            # Canonical requirements were already deduplicated upstream, so
            # use source-bound fallbacks instead of publishing a lossy merge.
            if len(declared_ids) != 1:
                continue
            for r_id in declared_ids:
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
                    if "admin" in req_text_lower:
                        actor = "admin"
                    elif "sales representative" in req_text_lower:
                        actor = "sales representative"
                    elif "viewer" in req_text_lower:
                        actor = "viewer"
                    elif set(_normalize_labels(getattr(req, "labels", None))).issubset({"NFR", "BR"}):
                        actor = "system"
                    else:
                        actor = "user"
                
                if not goal:
                    goal = "satisfy this requirement"

                agile_actor = normalize_actor_to_agile_role(actor)
                
                user_story = UserStory(
                    id=story_id,
                    title=f"Story for requirement {req.id}",
                    description=(
                        f"As {agile_actor}, I want {goal}, so that the documented "
                        "requirement is fulfilled."
                    ),
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
        requirement_coverages = rebuild_requirement_coverages(
            classified,
            final_stories,
            requirement_coverages,
        )

        # Validate generated stories and surface an aggregate quality warning.
        # We flag (not mutate) LLM stories so coverage stays consistent; the
        # fallback path already emits source-specific criteria.
        result_payload: dict = {
            "user_stories": final_stories,
            "requirement_coverages": requirement_coverages,
        }
        reqs_by_id = {r.id: r for r in classified}
        issues_by_story = validate_stories(final_stories, reqs_by_id)
        duplicate_ids = find_duplicate_story_ids(final_stories)
        if issues_by_story or duplicate_ids:
            quality_rule_map = {
                "unsupported_acceptance_fact": ("acceptance_criterion_unsupported_fact", "medium"),
                "missing_source_clause": ("acceptance_criteria_missing_source_clause", "medium"),
                "incorrect_story_requirement_mapping": ("incorrect_story_requirement_mapping", "high"),
                "missing_source_requirement_ids": ("story_missing_source_ids", "high"),
                "all_generic_acceptance_criteria": ("generic_acceptance_criteria", "medium"),
                "duplicate_acceptance_criteria": ("duplicate_acceptance_criterion", "medium"),
                "missing_title": ("story_empty_title", "high"),
                "insufficient_acceptance_criteria": ("story_missing_acceptance", "medium"),
            }
            generated_issues = list(state.get("quality_issues", []) or [])
            story_indexes = {
                story.id: index
                for index, story in enumerate(final_stories, start=1)
            }
            for story_id, codes_for_story in issues_by_story.items():
                for code in codes_for_story:
                    mapped = quality_rule_map.get(code)
                    if mapped is None:
                        continue
                    rule, severity = mapped
                    story_index = story_indexes.get(story_id)
                    if story_index is None:
                        # Validation is diagnostic and must never turn a
                        # successful generation into a total fallback because
                        # an internal story identifier is unavailable.
                        continue
                    generated_issues.append(QualityIssue(
                        item_id=story_index,
                        item_type="story",
                        severity=severity,
                        rule_violated=rule,
                        details=f"Generated story failed validation: {code}.",
                    ))
            if generated_issues:
                result_payload["quality_issues"] = generated_issues
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
                if "admin" in req_text_lower:
                    actor = "admin"
                elif "sales representative" in req_text_lower:
                    actor = "sales representative"
                elif "viewer" in req_text_lower:
                    actor = "viewer"
                elif set(_normalize_labels(getattr(req, "labels", None))).issubset({"NFR", "BR"}):
                    actor = "system"
                else:
                    actor = "user"
            
            if not goal:
                goal = "satisfy this requirement"

            agile_actor = normalize_actor_to_agile_role(actor)
            fallback_title, _ = _source_bound_story_wording([req])
            if not fallback_title or fallback_title.startswith("Story for"):
                fallback_title = f"Story for requirement {req.id}"

            fallback_description = (
                f"As {agile_actor}, I want {goal}, so that the documented "
                "requirement is fulfilled."
            )

            user_story = UserStory(
                id=story_id,
                title=fallback_title,
                description=fallback_description,
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
