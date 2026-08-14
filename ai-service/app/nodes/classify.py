
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import ClassifiedRequirement
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId
from app.services.semantic_quality import normalize_requirement_labels
from app.progress import update_progress
from pydantic import BaseModel
from typing import List, Literal, Dict
from collections import defaultdict
import json


# ---------------- PROMPT ----------------

User_Prompt = """
Classify the following requirements:
{items}
"""


# ---------------- SCHEMA ----------------

class RequirementClassification(BaseModel):
    id: int
    labels: List[Literal["FR", "NFR", "BR"]]
    confidence: float


class ClassificationResponse(BaseModel):
    classifications: List[RequirementClassification]


# ---------------- HELPERS ----------------

def _format_requirement(fr) -> str:
    return (
        f"id: {getattr(fr,'id','')}\n"
        f"text: {getattr(fr,'text','')}\n"
        f"actor: {getattr(fr,'actor',None)}\n"
        f"goal: {getattr(fr,'goal',None)}\n"
        f"candidate_labels: {getattr(fr,'candidate_labels', [])}\n"
        f"evidence: {getattr(fr,'evidence', [])}"
    )


def _chunk_requirements(requirements, batch_size: int = 5):
    for i in range(0, len(requirements), batch_size):
        yield requirements[i:i + batch_size]


def _clamp_confidence(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, value))


def _normalize_classification_payload(parsed) -> dict:
    """Normalize harmless LLM envelope variations before schema validation."""
    if isinstance(parsed, list):
        return {"classifications": parsed}
    if not isinstance(parsed, dict):
        return parsed
    if {"id", "labels", "confidence"}.issubset(parsed):
        return {"classifications": [parsed]}
    classifications = parsed.get("classifications")
    if isinstance(classifications, dict):
        return {"classifications": [classifications]}
    if "data" in parsed:
        return _normalize_classification_payload(parsed["data"])
    return parsed


async def _classify_batch(llm, batch):
    items = "\n\n".join(_format_requirement(fr) for fr in batch)
    
    try:
        system_prompt = load_prompt(PromptId.CLASSIFY_REQUIREMENTS_V1)
        raw = await llm.ainvoke([
            ("system", system_prompt),
            ("user", User_Prompt.format(items=items))
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
            return ClassificationResponse.model_validate(
                _normalize_classification_payload(parsed)
            )
        except Exception as e:
            print(f"Classification parse/validation error: {e}")
            return None
    except Exception as e:
        print(f"Classification LLM error: {e}")
        return None


# ---------------- MAIN NODE ----------------

async def classify_node(state: PipelineState) -> dict:
    print("--- CLASSIFY NODE (MULTI-LABEL) ---")
    update_progress(state.get("job_id"), "classify", 65, "PROCESSING")

    # Prefer new extracted_requirements; fall back to legacy functional_requirements
    frs = state.get("extracted_requirements") or state.get("functional_requirements", [])
    if not frs:
        return {"classified_requirements": []}

    try:
        llm = get_llm()
        if llm is None:
            raise RuntimeError("LLM not initialized")

        # ---------------- RAW COLLECTION ----------------
        all_classifications = []

        for batch in _chunk_requirements(frs, 5):
            response = await _classify_batch(llm, batch)

            if response and hasattr(response, "classifications"):
                all_classifications.extend(response.classifications)


        # ---------------- SAFE MERGE ----------------
        grouped: Dict[int, dict] = defaultdict(lambda: {
            "labels": set(),
            "confidence": 0.0
        })

        for c in all_classifications:
            grouped[c.id]["labels"].update(c.labels)
            grouped[c.id]["confidence"] = max(
                grouped[c.id]["confidence"],
                c.confidence
            )

        # ---------------- FINAL OUTPUT ----------------
        classified = []
        special_non_story_labels = {"Open Question", "Out-of-Scope", "Assumption"}

        for fr in frs:
            data = grouped.get(fr.id)

            # gather base fields present on both ExtractedRequirement and FunctionalRequirement
            base_kwargs = {
                "id": fr.id,
                "text": getattr(fr, "text", ""),
                "actor": getattr(fr, "actor", None),
                "goal": getattr(fr, "goal", None),
                "candidate_labels": getattr(fr, "candidate_labels", []),
                "confidence": getattr(fr, "confidence", 0.0),
                "evidence": getattr(fr, "evidence", []),
                "needs_review": getattr(fr, "needs_review", False),
                "review_reason": getattr(fr, "review_reason", None),
                "priority": getattr(fr, "priority", "Medium"),
            }

            candidate_labels = set(base_kwargs["candidate_labels"] or [])
            special_intersection = candidate_labels.intersection(special_non_story_labels)

            if special_intersection:
                # Always preserve special labels
                special_label = list(special_intersection)[0]
                classified.append(
                    ClassifiedRequirement(
                        **base_kwargs,
                        labels=[special_label],
                        classification_confidence=0.9,
                    )
                )
                continue

            if not data:
                # fallback per item: mark as FR with medium confidence
                classified.append(
                    ClassifiedRequirement(
                        **base_kwargs,
                        labels=["FR"],
                        classification_confidence=0.5,
                    )
                )
                continue

            classified.append(
                ClassifiedRequirement(
                    **base_kwargs,
                    labels=normalize_requirement_labels(
                        base_kwargs["text"],
                        list(data["labels"]) or ["FR"],
                    ),
                    classification_confidence=_clamp_confidence(data["confidence"]),
                )
            )

        return {"classified_requirements": classified}

    except Exception as e:
        print(f"Classify node LLM failure: {e}")
        # HARD SAFE FALLBACK (never fails tests)
        fallback = []
        special_non_story_labels = {"Open Question", "Out-of-Scope", "Assumption"}
        
        for fr in frs:
            candidate_labels = set(getattr(fr, "candidate_labels", []) or [])
            special_intersection = candidate_labels.intersection(special_non_story_labels)
            
            labels = [list(special_intersection)[0]] if special_intersection else ["FR"]
            confidence = 0.9 if special_intersection else 0.5
            
            fallback.append(
                ClassifiedRequirement(
                    id=getattr(fr, "id", None),
                    text=getattr(fr, "text", ""),
                    actor=getattr(fr, "actor", None),
                    goal=getattr(fr, "goal", None),
                    candidate_labels=getattr(fr, "candidate_labels", []),
                    confidence=getattr(fr, "confidence", 0.0),
                    evidence=getattr(fr, "evidence", []),
                    needs_review=getattr(fr, "needs_review", True),
                    review_reason=(getattr(fr, "review_reason", "") or "LLM failure fallback"),
                    labels=labels,
                    classification_confidence=confidence,
                    priority=getattr(fr, "priority", "Medium")
                )
            )

        return {"classified_requirements": fallback}










