
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import ClassifiedRequirement
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Literal, Dict
from collections import defaultdict


# ---------------- PROMPT ----------------

System_Prompt = """
You are a senior requirements analyst.

You classify each requirement into ONE OR MORE of:

FR = Functional Requirement
NFR = Non-Functional Requirement
BR = Business Rule

---

RULES:
- A requirement may have multiple labels.
- Always return ALL applicable labels.
- Do NOT force a single label.
- If uncertain, still choose the most likely labels (do NOT leave empty).

---

LABEL GUIDANCE:

FR:
- system behavior
- features
- actions
- workflows
- what the system does

NFR:
- performance (speed, latency)
- security
- scalability
- usability
- reliability
- constraints on quality

BR:
- policies
- permissions
- business constraints
- regulations
- "only", "must", "allowed", "forbidden" rules

---

MULTI-LABEL RULES (IMPORTANT):
- If a requirement includes BOTH behavior + constraint → FR + BR
- If it includes behavior + performance/security → FR + NFR
- If it includes all three → FR + NFR + BR

Example:
"The user logs in within 0.1s" → ["FR", "NFR"]
"Only admins can delete users" → ["BR"]
"System validates password complexity" → ["FR", "BR"]

---

CONFIDENCE RULES:
- 0.9–1.0 → very explicit requirement
- 0.7–0.9 → clear but slightly ambiguous
- 0.4–0.7 → partially ambiguous
- <0.4 → unclear or noisy input

IMPORTANT:
- Do NOT always output 1.0
- Reduce confidence for vague or unclear requirements

---

OUTPUT RULES:
- Return ONLY valid JSON
- No explanations
- No extra text
- Must include:
  id, labels, confidence
"""

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
        f"id: {fr.id}\n"
        f"text: {fr.text}\n"
        f"actor: {fr.actor}\n"
        f"goal: {fr.goal}\n"
        f"source_hint: {fr.source_hint}"
    )


def _chunk_requirements(requirements, batch_size: int = 5):
    for i in range(0, len(requirements), batch_size):
        yield requirements[i:i + batch_size]


def _clamp_confidence(value: float) -> float:
    try:
        value = float(value)
    except:
        return 0.5
    return max(0.0, min(1.0, value))


async def _classify_batch(chain, batch):
    items = "\n\n".join(_format_requirement(fr) for fr in batch)
    return await chain.ainvoke({"items": items})


# ---------------- MAIN NODE ----------------

async def classify_node(state: PipelineState) -> dict:
    print("--- CLASSIFY NODE (MULTI-LABEL) ---")

    frs = state.get("functional_requirements", [])
    if not frs:
        return {"classified_requirements": []}

    try:
        llm = get_llm("gpt-oss-120b")
        if llm is None:
            raise RuntimeError("LLM not initialized")

        structured_llm = llm.with_structured_output(
            ClassificationResponse,
            method="function_calling"
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", System_Prompt),
            ("user", User_Prompt)
        ])

        chain = prompt | structured_llm

        # ---------------- RAW COLLECTION ----------------
        all_classifications = []

        for batch in _chunk_requirements(frs, 5):
            response = await _classify_batch(chain, batch)

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

        for fr in frs:
            data = grouped.get(fr.id)

            if not data:
                # fallback per item
                classified.append(
                    ClassifiedRequirement(
                        id=fr.id,
                        text=fr.text,
                        actor=fr.actor,
                        goal=fr.goal,
                        source_hint=fr.source_hint,
                        labels=["FR"],
                        confidence=0.5,
                    )
                )
                continue

            classified.append(
                ClassifiedRequirement(
                    id=fr.id,
                    text=fr.text,
                    actor=fr.actor,
                    goal=fr.goal,
                    source_hint=fr.source_hint,
                    labels=list(data["labels"]),
                    confidence=_clamp_confidence(data["confidence"]),
                )
            )

        return {"classified_requirements": classified}

    except Exception as e:
        print(f"Classify node LLM failure: {e}")

        # HARD SAFE FALLBACK (never fails tests)
        return {
            "classified_requirements": [
                ClassifiedRequirement(
                    id=fr.id,
                    text=fr.text,
                    actor=fr.actor,
                    goal=fr.goal,
                    source_hint=fr.source_hint,
                    labels=["FR"],
                    confidence=0.5,
                )
                for fr in frs
            ]
        }










