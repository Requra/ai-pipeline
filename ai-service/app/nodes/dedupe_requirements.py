"""
dedupe_requirements node.

Chunk overlap, repeated document sections, and LLM repetition all produce the
same requirement more than once. This node merges exact and near-duplicate
requirements *before* classification/generation so downstream nodes operate on a
clean set.

Merge policy (never lose grounding):
  * Exact (normalized-text) or near-duplicate (token Jaccard >= threshold) merge.
  * Union evidence spans (dedup by chunk_id+quote) — evidence is never dropped.
  * Keep the highest confidence and the strongest priority.
  * Union candidate labels; OR needs_review; fill missing actor/goal.
  * Do NOT merge when both requirements name a different actor — same feature for
    a different actor is a distinct requirement (flagged POSSIBLE_DUPLICATE_REVIEW).
  * Reassign stable sequential ids and refresh the legacy projection.
"""

from __future__ import annotations

import re
import json
import logging
import asyncio
from typing import List, Optional, Tuple

from app.nodes.extract import project_legacy_requirements
from app.progress import update_progress
from app.rag.scoring import tokenize
from app.services.semantic_quality import fact_tokens, meaningful_tokens
from app.schemas.items import ExtractedRequirement, PipelineWarning, QualityIssue
from app.schemas.pipeline_state import PipelineState
from app.config import settings
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from app.rag.requirement_embeddings import RequirementEmbeddingService

CONFLICT_USER_PROMPT = """
Analyze the following requirement pairs for semantic conflicts. 

Requirements List:
{requirements_text}

Candidate Pairs to Analyze:
{pairs_text}
"""

logger = logging.getLogger("app.nodes.dedupe_requirements")

# Token-set Jaccard at/above this is considered a near-duplicate.
NEAR_DUPLICATE_THRESHOLD = 0.8

_PRIORITY_RANK = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}


def _normalize_text(text: str) -> str:
    """Canonical form for exact-duplicate detection."""
    if not text:
        return ""
    lowered = re.sub(r"[^a-z0-9\s]+", " ", text.lower())
    return re.sub(r"\s+", " ", lowered).strip()


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _norm_actor(actor: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (actor or "").strip().lower())


def _actors_conflict(a: ExtractedRequirement, b: ExtractedRequirement) -> bool:
    """True only when both name a (different) actor — a real semantic conflict."""
    na, nb = _norm_actor(a.actor), _norm_actor(b.actor)
    if na and nb and na != nb:
        # "user"/"users" style differences should not count as a conflict.
        if na.rstrip("s") == nb.rstrip("s"):
            return False
        technical_terms = {
            "system", "service", "application", "workspace", "portal",
            "platform", "api", "database",
        }
        # LLM extraction frequently alternates between "system" and a named
        # technical component for the same source proposition.
        if any(term in na for term in technical_terms) and any(
            term in nb for term in technical_terms
        ):
            return False
        return True
    return False


def _evidence_document_ids(req: ExtractedRequirement) -> set[str]:
    return {
        evidence.document_id
        for evidence in (getattr(req, "evidence", []) or [])
        if getattr(evidence, "document_id", None)
    }


def _source_intent_matches(a: ExtractedRequirement, b: ExtractedRequirement) -> bool:
    """Allow cross-source dedupe only for an explicitly matching proposition.

    A high token overlap alone is not authority to merge requirements from two
    uploads: similar wording can describe separate systems or policies. Exact
    repeated text is safe. Paraphrases across sources additionally need the
    same actor intent and a comparable extracted goal.
    """
    a_docs, b_docs = _evidence_document_ids(a), _evidence_document_ids(b)
    if not a_docs or not b_docs or a_docs & b_docs:
        return True
    if _normalize_text(a.text) == _normalize_text(b.text):
        return True
    if _actors_conflict(a, b):
        return False
    a_goal, b_goal = fact_tokens(a.goal or ""), fact_tokens(b.goal or "")
    if not a_goal or not b_goal:
        return False
    return _jaccard(a_goal, b_goal) >= 0.75


def _numeric_constraint_context(text: str) -> set[str]:
    """Return the measurement concepts surrounding numeric constraints."""
    token_matches = list(re.finditer(r"[$€£]?\d[\d,]*(?:\.\d+)?|[a-z]+", (text or "").lower()))
    numeric_positions = [
        index for index, match in enumerate(token_matches)
        if re.fullmatch(r"[$€£]?\d[\d,]*(?:\.\d+)?", match.group(0))
    ]
    context: set[str] = set()
    for index in numeric_positions:
        start = max(0, index - 6)
        end = min(len(token_matches), index + 7)
        window = " ".join(match.group(0) for match in token_matches[start:end])
        context.update(fact_tokens(window))
    return context - {
        "asset", "standard", "system", "user", "shall", "must", "allow",
        "requirement", "request", "number", "value", "than", "under", "over",
    }


def _orthogonal_numeric_constraints(a: ExtractedRequirement, b: ExtractedRequirement) -> bool:
    """True when two numeric rules constrain different workflow dimensions.

    Sharing an entity or workflow does not make a monetary threshold conflict
    with a quantity, duration, availability, or performance limit. An LLM may
    propose a conflict only when the measurement contexts materially overlap.
    """
    a_context = _numeric_constraint_context(a.text)
    b_context = _numeric_constraint_context(b.text)
    if not a_context or not b_context:
        return False
    return len(a_context & b_context) < 2


def _conditional_approval_and_quantity_are_composable(
    a: ExtractedRequirement,
    b: ExtractedRequirement,
) -> bool:
    """Recognise compatible approval gates and independent quantity limits.

    An approval threshold and a maximum number of items can both govern the
    same request. Treating that pair as a scope conflict creates a misleading
    question and reduces the quality score. We only override an LLM conflict
    label when neither side explicitly grants an exemption or negates the
    other rule.
    """
    texts = ((a.text or "").lower(), (b.text or "").lower())
    if any(re.search(
        r"\b(?:no|not|never|without|exempt|except)\b|"
        r"(?:\u0628\u062f\u0648\u0646|\u0627\u0633\u062a\u062b\u0646\u0627\u0621)",
        text,
    ) for text in texts):
        return False
    approval = re.compile(
        r"\b(?:approv(?:e|al)|authori[sz](?:e|ation)|permission|consent)\b|"
        r"(?:\u0645\u0648\u0627\u0641\u0642\u0629|\u062a\u0635\u0631\u064a\u062d|\u0627\u0639\u062a\u0645\u0627\u062f)",
        re.IGNORECASE,
    )
    conditional = re.compile(
        r"\b(?:if|when|unless|above|below|over|under|exceeds?|at\s+least|"
        r"more\s+than|less\s+than)\b|"
        r"(?:\u0625\u0630\u0627|\u0639\u0646\u062f|\u064a\u062a\u062c\u0627\u0648\u0632|\u0623\u0643\u062b\u0631\s+\u0645\u0646)",
        re.IGNORECASE,
    )
    quantity = re.compile(
        r"\b(?:up\s+to|at\s+most|maximum|max(?:imum)?|simultaneous(?:ly)?|"
        r"number\s+of|quantity)\b|"
        r"(?:\u062d\u062a\u0649|\u0627\u0644\u062d\u062f\s+\u0627\u0644\u0623\u0642\u0635\u0649|\u0639\u062f\u062f)",
        re.IGNORECASE,
    )
    approval_text = next((text for text in texts if approval.search(text)), "")
    quantity_text = next((text for text in texts if quantity.search(text)), "")
    return bool(approval_text and quantity_text and conditional.search(approval_text))


_DISPOSITION_RANK = {
    "accepted": 4,
    "deferred": 3,
    "proposed": 2,
    "uncertain": 1,
    "rejected": 0,
}


def _higher_priority(p1: str, p2: str) -> str:
    return p1 if _PRIORITY_RANK.get(p1, 1) >= _PRIORITY_RANK.get(p2, 1) else p2


def _merge_into(base: ExtractedRequirement, other: ExtractedRequirement) -> None:
    """Fold ``other`` into ``base`` in place, preserving all grounding."""
    # Evidence union (dedup by chunk_id + quote).
    seen = {(e.chunk_id, e.quote) for e in base.evidence}
    for ev in other.evidence:
        key = (ev.chunk_id, ev.quote)
        if key not in seen:
            base.evidence.append(ev)
            seen.add(key)

    base.confidence = max(base.confidence, other.confidence)
    base.priority = _higher_priority(base.priority, other.priority)

    # Disposition precedence
    base_disp = getattr(base, "disposition", "accepted")
    other_disp = getattr(other, "disposition", "accepted")
    if _DISPOSITION_RANK.get(other_disp, 0) > _DISPOSITION_RANK.get(base_disp, 0):
        base.disposition = other_disp

    for label in other.candidate_labels:
        if label not in base.candidate_labels:
            base.candidate_labels.append(label)
    if hasattr(base, "labels") and hasattr(other, "labels"):
        for label in getattr(other, "labels", []) or []:
            if label not in base.labels:
                base.labels.append(label)
        base.classification_confidence = max(
            getattr(base, "classification_confidence", 0.0),
            getattr(other, "classification_confidence", 0.0),
        )

    base.actor = base.actor or other.actor
    base.goal = base.goal or other.goal
    if "explicit" in (base.extraction_type, other.extraction_type):
        base.extraction_type = "explicit"
    elif base.extraction_type is None:
        base.extraction_type = other.extraction_type

    base.needs_review = bool(base.needs_review or other.needs_review)
    reasons = [r for r in (base.review_reason, other.review_reason) if r]
    if reasons:
        base.review_reason = " ".join(dict.fromkeys(reasons))


def _containment_duplicate(a: ExtractedRequirement, b: ExtractedRequirement) -> bool:
    """Return whether one extraction is an atomic subset of the other."""
    tokens_a, tokens_b = fact_tokens(a.text), fact_tokens(b.text)
    if min(len(tokens_a), len(tokens_b)) < 4 or len(tokens_a) == len(tokens_b):
        return False
    smaller, larger, larger_text = (
        (tokens_a, tokens_b, b.text)
        if len(tokens_a) < len(tokens_b)
        else (tokens_b, tokens_a, a.text)
    )
    # A source-level composite normally joins clauses with a conjunction or
    # semicolon. Requiring that marker avoids collapsing merely related peers.
    is_composite = (
        ";" in (larger_text or "")
        or "," in (larger_text or "")
        or bool(re.search(r"\b(?:and|or)\b", larger_text or "", re.I))
    )
    shared = smaller & larger
    # One source window can yield a composite rule and an atomic restatement of
    # its mandated action. The restatement may add only a purpose clause, so it
    # has less token overlap than a textual paraphrase but no new testable rule.
    return (
        is_composite
        and len(shared) >= 4
        and len(shared) / len(smaller) >= 0.60
    )


def _canonical_components(
    reqs: List[ExtractedRequirement],
) -> tuple[List[List[int]], List[int]]:
    """Cluster exact, near, and atomic/composite duplicate extractions."""
    parent = list(range(len(reqs)))
    actor_review_ids: List[int] = []

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    norms = [_normalize_text(req.text) for req in reqs]
    token_sets = [fact_tokens(req.text) for req in reqs]
    for left in range(len(reqs)):
        for right in range(left + 1, len(reqs)):
            left_disp = getattr(reqs[left], "disposition", "accepted")
            right_disp = getattr(reqs[right], "disposition", "accepted")

            # Conflicting disposition (accepted vs rejected): never merge
            if (left_disp == "accepted" and right_disp == "rejected") or (
                left_disp == "rejected" and right_disp == "accepted"
            ):
                reqs[right].needs_review = True
                reqs[left].needs_review = True
                note = "[DISPOSITION_CONFLICT: accepted requirement conflicts with rejected proposal]"
                reqs[right].review_reason = " ".join(
                    part for part in (reqs[right].review_reason, note) if part
                )
                reqs[left].review_reason = " ".join(
                    part for part in (reqs[left].review_reason, note) if part
                )
                actor_review_ids.append(reqs[right].id)
                continue

            exact = bool(norms[left]) and norms[left] == norms[right]
            near = (
                min(len(token_sets[left]), len(token_sets[right])) >= 4
                and _jaccard(token_sets[left], token_sets[right])
                >= NEAR_DUPLICATE_THRESHOLD
            )
            contained = _containment_duplicate(reqs[left], reqs[right])
            source_intent_matches = _source_intent_matches(reqs[left], reqs[right])
            if (exact or contained) and source_intent_matches:
                # Exact text with different actor fields is an extraction
                # inconsistency, not a second source proposition.
                union(left, right)
            elif near and source_intent_matches:
                if _actors_conflict(reqs[left], reqs[right]):
                    reqs[right].needs_review = True
                    note = "[POSSIBLE_DUPLICATE: similar proposition has a materially different actor]"
                    reqs[right].review_reason = " ".join(
                        part for part in (reqs[right].review_reason, note) if part
                    )
                    actor_review_ids.append(reqs[right].id)
                else:
                    union(left, right)
            elif exact or contained or near:
                # Keep ambiguous cross-source overlap separate. It is safer
                # to expose two traceable requirements than silently merge two
                # potentially independent policies.
                reqs[right].needs_review = True
                note = "[POSSIBLE_CROSS_SOURCE_DUPLICATE: similar wording from distinct source intent]"
                reqs[right].review_reason = " ".join(
                    part for part in (reqs[right].review_reason, note) if part
                )
                actor_review_ids.append(reqs[right].id)

    grouped: dict[int, List[int]] = {}
    for index in range(len(reqs)):
        grouped.setdefault(find(index), []).append(index)
    return list(grouped.values()), actor_review_ids


def canonicalize_requirements(
    reqs: List[ExtractedRequirement],
    *,
    reassign_ids: bool = True,
) -> tuple[List[ExtractedRequirement], int, dict[int, int], List[int]]:
    """Canonicalize requirements and return a stable old-to-new ID mapping.

    The helper is reusable from existing nodes, so a final deterministic pass
    can run immediately before story generation without adding a graph node or
    changing the public response contract.
    """
    if len(reqs) <= 1:
        identity = {req.id: req.id for req in reqs}
        return list(reqs), 0, identity, []

    original_ids = {id(req): req.id for req in reqs}
    components, possible_dup_ids = _canonical_components(reqs)
    canonical: List[ExtractedRequirement] = []
    component_old_ids: List[List[int]] = []
    merged_count = 0

    for component in components:
        representative_index = max(
            component,
            key=lambda index: (
                len(meaningful_tokens(reqs[index].text)),
                len(reqs[index].text or ""),
                reqs[index].confidence,
                -index,
            ),
        )
        representative = reqs[representative_index]
        old_ids = [original_ids[id(reqs[index])] for index in component]
        for index in component:
            if index == representative_index:
                continue
            _merge_into(representative, reqs[index])
            merged_count += 1
        canonical.append(representative)
        component_old_ids.append(old_ids)

    source_position = {id(req): index for index, req in enumerate(reqs)}
    ordering = sorted(
        range(len(canonical)),
        key=lambda index: source_position[id(canonical[index])],
    )
    canonical = [canonical[index] for index in ordering]
    component_old_ids = [component_old_ids[index] for index in ordering]

    old_to_new: dict[int, int] = {}
    for position, (req, old_ids) in enumerate(
        zip(canonical, component_old_ids),
        start=1,
    ):
        new_id = position if reassign_ids or merged_count else old_ids[0]
        req.id = new_id
        for old_id in old_ids:
            old_to_new[old_id] = new_id

    return canonical, merged_count, old_to_new, possible_dup_ids


# --- Conflict Detection Helpers ---


def _find_semantic_candidates(
    reqs: List[ExtractedRequirement], 
    threshold: float = 0.55,
    k: int = 5
) -> List[Tuple[ExtractedRequirement, ExtractedRequirement, float]]:
    """
    Find Top-K semantically similar requirement pairs using cosine similarity.
    Performs comparisons over only the upper triangle of pairs.
    """
    pairs_sims = []
    for i in range(len(reqs)):
        req_a = reqs[i]
        if not req_a.embedding:
            continue
        for j in range(i + 1, len(reqs)):
            req_b = reqs[j]
            if not req_b.embedding:
                continue
            sim = RequirementEmbeddingService.similarity(req_a.embedding, req_b.embedding)
            if sim >= threshold:
                pairs_sims.append((sim, req_a, req_b))

    # Sort all candidate pairs by similarity descending
    pairs_sims.sort(key=lambda x: x[0], reverse=True)

    # Distribute matches to limit candidates to Top-K per requirement to avoid hotspots
    candidates = []
    req_match_counts = {}
    for sim, req_a, req_b in pairs_sims:
        count_a = req_match_counts.get(req_a.id, 0)
        count_b = req_match_counts.get(req_b.id, 0)
        if count_a < k and count_b < k:
            req_match_counts[req_a.id] = count_a + 1
            req_match_counts[req_b.id] = count_b + 1
            candidates.append((req_a, req_b, sim))
            
    return candidates


def _find_jaccard_candidates(
    reqs: List[ExtractedRequirement], 
    low_threshold: float = 0.3, 
    high_threshold: float = 0.8
) -> List[Tuple[ExtractedRequirement, ExtractedRequirement, float]]:
    candidates = []
    seen_pairs = set()
    token_sets = {req.id: set(tokenize(req.text)) for req in reqs}
    
    for i, req_a in enumerate(reqs):
        tokens_a = token_sets[req_a.id]
        for j, req_b in enumerate(reqs):
            if i >= j:
                continue
            tokens_b = token_sets[req_b.id]
            sim = _jaccard(tokens_a, tokens_b)
            if low_threshold <= sim < high_threshold:
                pair_key = (req_a.id, req_b.id)
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    candidates.append((req_a, req_b, sim))
                    
    return candidates


def _format_req_id(req_id: int) -> str:
    return f"REQ-{str(req_id).zfill(3)}"


def _parse_req_int_id(req_str_id: str) -> Optional[int]:
    match = re.search(r"\d+", str(req_str_id))
    return int(match.group(0)) if match else None


def _normalize_classification(raw_class: str) -> str:
    """Robustly normalize LLM classification strings into standard uppercase SNAKE_CASE."""
    if not raw_class:
        return "INDEPENDENT"
    # Convert CamelCase to snake_case
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', raw_class)
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    # Lowercase, clean punctuation/spaces, convert to upper
    cleaned = s2.replace(" ", "_").replace("-", "_").upper()
    return re.sub(r"_+", "_", cleaned).strip("_")


def _normalize_resolution_options(value, classification: str, req_a_id: str, req_b_id: str) -> List[str]:
    """Validate advisory options and provide safe deterministic fallbacks."""
    options: List[str] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                continue
            cleaned = re.sub(r"\s+", " ", item).strip()
            if cleaned and cleaned not in options:
                options.append(cleaned[:300])
            if len(options) == 3:
                break
    if classification in {"INDEPENDENT", "DUPLICATE"}:
        return options
    if len(options) >= 2:
        return options
    return [
        f"Clarify the intended scope and precedence between {req_a_id} and {req_b_id} with the requirement owner.",
        f"Revise {req_a_id} and {req_b_id} into an explicit, testable rule that removes the ambiguity.",
    ]


def _batch_candidates_by_tokens(
    candidates: List[Tuple[ExtractedRequirement, ExtractedRequirement, float]],
    max_tokens: int = 2500
) -> List[List[Tuple[ExtractedRequirement, ExtractedRequirement, float]]]:
    """Batch candidate pairs based on estimated prompt token count (characters / 4)."""
    batches = []
    current_batch = []
    current_char_count = 0
    max_chars = max_tokens * 4
    
    current_reqs = set()
    
    for req_a, req_b, sim in candidates:
        pair_chars = len(f"Compare REQ-{req_a.id} and REQ-{req_b.id}\n")
        req_chars = 0
        if req_a.id not in current_reqs:
            req_chars += len(req_a.text)
        if req_b.id not in current_reqs:
            req_chars += len(req_b.text)
            
        total_added_chars = pair_chars + req_chars
        
        if current_batch and (current_char_count + total_added_chars > max_chars):
            batches.append(current_batch)
            current_batch = [(req_a, req_b, sim)]
            current_char_count = total_added_chars
            current_reqs = {req_a.id, req_b.id}
        else:
            current_batch.append((req_a, req_b, sim))
            current_char_count += total_added_chars
            current_reqs.add(req_a.id)
            current_reqs.add(req_b.id)
            
    if current_batch:
        batches.append(current_batch)
        
    return batches


async def _classify_conflicts_batch(
    llm, 
    batch: List[Tuple[ExtractedRequirement, ExtractedRequirement, float]]
) -> List[dict]:
    unique_reqs = {}
    for req_a, req_b, _ in batch:
        unique_reqs[req_a.id] = req_a
        unique_reqs[req_b.id] = req_b
        
    reqs_lines = []
    for req_id, req in sorted(unique_reqs.items()):
        reqs_lines.append(f"[{_format_req_id(req_id)}] Text: {req.text} (Actor: {req.actor or 'System'}, Priority: {req.priority})")
    requirements_text = "\n".join(reqs_lines)
    
    pairs_lines = []
    for idx, (req_a, req_b, sim) in enumerate(batch, start=1):
        pairs_lines.append(f"Pair {idx}: Compare {_format_req_id(req_a.id)} and {_format_req_id(req_b.id)} (Similarity: {sim:.2f})")
    pairs_text = "\n".join(pairs_lines)
    
    try:
        # Provider network call timeout wrapper
        timeout = getattr(settings, "PROVIDER_TIMEOUT_SECONDS", 120)
        system_prompt = load_prompt(PromptId.DETECT_CONFLICTS_V1)
        raw = await asyncio.wait_for(
            llm.ainvoke([
                ("system", system_prompt),
                ("user", CONFLICT_USER_PROMPT.format(requirements_text=requirements_text, pairs_text=pairs_text))
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
            
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict) and "conflicts" in parsed:
                return parsed["conflicts"]
            return []
        except Exception as e:
            logger.warning("Failed to parse LLM conflict classification JSON: %s", e)
            return []
    except asyncio.TimeoutError:
        logger.warning("LLM conflict classification call timed out after %d seconds.", timeout)
        return []
    except Exception as e:
        logger.warning("LLM conflict classification API error: %s", e)
        return []



async def dedupe_requirements_node(state: PipelineState) -> dict:
    print("--- DEDUPE REQUIREMENTS NODE ---")
    update_progress(state.get("job_id"), "dedupe_requirements", 55, "PROCESSING")

    reqs: List[ExtractedRequirement] = state.get("extracted_requirements", []) or []
    if len(reqs) <= 1:
        return {}  # nothing to dedupe

    deduped, merged_count, _, possible_dup_ids = canonicalize_requirements(reqs)

    # --- Conflict Detection Phase ---
    candidates = []
    conflict_warnings: List[PipelineWarning] = []
    conflict_issues: List[QualityIssue] = []

    if settings.ENABLE_CONFLICT_DETECTION:
        if settings.ENABLE_EMBEDDINGS:
            try:
                await RequirementEmbeddingService.ensure_requirement_embeddings(deduped)
                candidates = _find_semantic_candidates(
                    deduped, 
                    threshold=settings.CONFLICT_SIMILARITY_THRESHOLD, 
                    k=settings.CONFLICT_TOP_K
                )
            except Exception as e:
                logger.warning("Failed semantic candidate retrieval: %s. Falling back to Jaccard.", e, exc_info=True)
                candidates = _find_jaccard_candidates(
                    deduped, 
                    low_threshold=settings.CONFLICT_JACCARD_LOW, 
                    high_threshold=settings.CONFLICT_JACCARD_HIGH
                )
        else:
            candidates = _find_jaccard_candidates(
                deduped, 
                low_threshold=settings.CONFLICT_JACCARD_LOW, 
                high_threshold=settings.CONFLICT_JACCARD_HIGH
            )

        if candidates:
            llm = get_llm()
            if llm is not None:
                all_raw_conflicts = []
                # Batch candidate pairs based on token budget
                for batch in _batch_candidates_by_tokens(candidates, max_tokens=2500):
                    raw_conflicts = await _classify_conflicts_batch(llm, batch)
                    all_raw_conflicts.extend(raw_conflicts)
                
                valid_classifications = {
                    "CONTRADICTION", "CONSTRAINT_CONFLICT", "PERMISSION_CONFLICT", 
                    "SCOPE_CONFLICT", "PRIORITY_CONFLICT", "COMPLEMENTARY", "DUPLICATE"
                }
                for conflict in all_raw_conflicts:
                    req_a_id = conflict.get("requirement_a")
                    req_b_id = conflict.get("requirement_b")
                    raw_classification = conflict.get("classification", "")
                    classification = _normalize_classification(raw_classification)
                    
                    try:
                        confidence = float(conflict.get("confidence", 0.0))
                    except Exception:
                        confidence = 0.0
                        
                    reason = conflict.get("reason", "")
                    question = conflict.get("clarification_question", "")
                    resolution_options = _normalize_resolution_options(
                        conflict.get("resolution_options"),
                        classification,
                        req_a_id,
                        req_b_id,
                    )
                    
                    if not req_a_id or not req_b_id:
                        continue
                    if classification == "INDEPENDENT" or confidence < settings.CONFLICT_MIN_CONFIDENCE:
                        continue
                    if classification not in valid_classifications:
                        continue

                    req_a_int = _parse_req_int_id(req_a_id)
                    req_b_int = _parse_req_int_id(req_b_id)
                    req_a = next((req for req in deduped if req.id == req_a_int), None)
                    req_b = next((req for req in deduped if req.id == req_b_int), None)
                    if (
                        classification in {"CONSTRAINT_CONFLICT", "SCOPE_CONFLICT"}
                        and req_a is not None
                        and req_b is not None
                        and (
                            _orthogonal_numeric_constraints(req_a, req_b)
                            or _conditional_approval_and_quantity_are_composable(req_a, req_b)
                        )
                    ):
                        classification = "COMPLEMENTARY"
                        reason = (
                            "The requirements constrain different measurable "
                            "dimensions of the same workflow and can both apply."
                        )
                        question = ""
                        resolution_options = []
                    
                    options_str = ""
                    if resolution_options and classification != "COMPLEMENTARY":
                        options_str = "\n  - Proposed Resolutions:\n" + "\n".join(f"    {idx}. {opt}" for idx, opt in enumerate(resolution_options, start=1))

                    if classification == "COMPLEMENTARY":
                        details = (
                            f"Related requirements {req_a_id} and {req_b_id} are complementary:\n"
                            f"  - Reason: {reason}"
                        )
                    else:
                        # Formatting warning and issues text cleanly
                        details = (
                            f"Conflict detected between {req_a_id} and {req_b_id}:\n"
                            f"  - Category: {classification}\n"
                            f"  - Reason: {reason}\n"
                            f"  - Clarification Question: {question}"
                            f"{options_str}"
                        )
                    
                    conflict_warnings.append(PipelineWarning(
                        node_name="dedupe_requirements",
                        code=f"SEMANTIC_{classification}",
                        message=details
                    ))
                    
                    # Complementary relationships are useful context, not a
                    # quality defect and do not require conflict resolution.
                    if req_a_int is not None and classification != "COMPLEMENTARY":
                        severity = "high" if classification in ("CONTRADICTION", "PERMISSION_CONFLICT") else "medium"
                        conflict_issues.append(QualityIssue(
                            item_id=req_a_int,
                            item_type="requirement",
                            severity=severity,
                            rule_violated=f"semantic_conflict_{classification.lower()}",
                            details=details
                        ))

    legacy = project_legacy_requirements(deduped)

    result: dict = {
        "extracted_requirements": deduped,
        "functional_requirements": legacy,
    }

    new_warnings: List[PipelineWarning] = []
    if merged_count:
        new_warnings.append(PipelineWarning(
            node_name="dedupe_requirements",
            code="DUPLICATE_REQUIREMENT_MERGED",
            message=f"Merged {merged_count} duplicate requirement(s) into canonical entries.",
        ))
    if possible_dup_ids:
        new_warnings.append(PipelineWarning(
            node_name="dedupe_requirements",
            code="POSSIBLE_DUPLICATE_REVIEW",
            message=(
                f"{len(possible_dup_ids)} requirement(s) look similar to others but "
                "differ by actor or source intent; kept separate and flagged for review."
            ),
        ))
    
    # Append conflict warnings and issues
    new_warnings.extend(conflict_warnings)
    if new_warnings:
        result["warnings"] = (state.get("warnings", []) or []) + new_warnings
        
    if conflict_issues:
        result["quality_issues"] = (state.get("quality_issues", []) or []) + conflict_issues

    return result
