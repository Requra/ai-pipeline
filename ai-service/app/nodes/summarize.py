import json
import logging
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import StructuredSummary
from app.llm import get_llm
from app.prompts.loader import load_prompt
from app.prompts.registry import PromptId

logger = logging.getLogger(__name__)

# Approximate character limit to stay within model context window.
# ~12k chars ≈ 3k-4k tokens, leaving room for the system prompt and output.
MAX_INPUT_CHARS = 12_000

_EMPTY_SUMMARY = StructuredSummary(
    executive_summary="",
    key_decisions=[],
    open_questions=[],
    risks=[],
    assumptions=[],
    action_items=[],
    stakeholders=[],
    scope=[],
    out_of_scope=[],
)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _safe_str_list(value) -> list[str]:
    """Coerce a value into a list of non-empty strings."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


def _parse_summary(content: str) -> StructuredSummary:
    """Parse LLM output into a StructuredSummary, tolerating common LLM quirks."""
    cleaned = _strip_code_fences(content)
    data = json.loads(cleaned)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    return StructuredSummary(
        executive_summary=str(data.get("executive_summary") or ""),
        key_decisions=_safe_str_list(data.get("key_decisions")),
        open_questions=_safe_str_list(data.get("open_questions")),
        risks=_safe_str_list(data.get("risks")),
        assumptions=_safe_str_list(data.get("assumptions")),
        action_items=_safe_str_list(data.get("action_items")),
        stakeholders=_safe_str_list(data.get("stakeholders")),
        scope=_safe_str_list(data.get("scope")),
        out_of_scope=_safe_str_list(data.get("out_of_scope")),
    )


def _truncate_for_context(text: str, max_chars: int = MAX_INPUT_CHARS) -> str:
    """Truncate long text, sampling from beginning and end to preserve context."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + "\n\n[... middle section truncated for length ...]\n\n"
        + text[-half:]
    )


def _build_artifact_digest(state) -> str:
    """Compact digest of the structured analysis so the summary reflects the
    extracted requirements/stories/open-questions, not just raw text."""
    reqs = state.get("classified_requirements") or state.get("extracted_requirements") or []
    stories = state.get("user_stories") or []

    req_lines: list[str] = []
    open_questions: list[str] = []
    for r in reqs[:40]:
        text = (getattr(r, "text", "") or "").strip()
        if not text:
            continue
        labels = getattr(r, "labels", None) or getattr(r, "candidate_labels", None) or []
        if "Open Question" in labels:
            open_questions.append(text)
        req_lines.append(f"- ({'/'.join(labels) or 'FR'}) {text[:200]}")

    story_lines = [
        f"- {(getattr(s, 'title', '') or '').strip()[:160]}"
        for s in stories[:40]
        if (getattr(s, "title", "") or "").strip()
    ]

    parts: list[str] = []
    if req_lines:
        parts.append("Extracted requirements:\n" + "\n".join(req_lines))
    if story_lines:
        parts.append("Generated user stories:\n" + "\n".join(story_lines))
    if open_questions:
        parts.append("Open questions:\n" + "\n".join(f"- {q[:200]}" for q in open_questions[:20]))
    return "\n\n".join(parts)


from app.progress import update_progress

async def summarize_node(state: PipelineState) -> dict:
    """
    Generate structured summary matching `StructuredSummary` contract.
    Parses LLM JSON output to fill all 9 fields.
    """
    print("--- SUMMARIZE NODE ---")
    update_progress(state.get("job_id"), "summarize", 95, "PROCESSING")
    raw_text = state.get("raw_text", "") or ""
    digest = _build_artifact_digest(state)

    # Nothing to summarize at all.
    if not raw_text and not digest:
        return {"summary": _EMPTY_SUMMARY}

    try:
        llm = get_llm()
        system_prompt = load_prompt(PromptId.SUMMARIZE_STRUCTURED_V1)

        # Truncate the raw document, then append the structured digest so the
        # summary reflects requirements/stories/open-questions as well.
        input_text = _truncate_for_context(raw_text)
        user_sections = []
        if input_text:
            user_sections.append(f"Source document:\n{input_text}")
        if digest:
            user_sections.append(f"Structured analysis extracted so far:\n{digest}")
        user_message = "Analyze and summarize this software project.\n\n" + "\n\n".join(user_sections)

        raw = await llm.ainvoke([
            ("system", system_prompt),
            ("user", user_message),
        ])

        content = getattr(raw, "content", None) or str(raw)

        try:
            summary = _parse_summary(content)
            return {"summary": summary}
        except (json.JSONDecodeError, ValueError) as parse_err:
            logger.warning("Summarize JSON parse failed: %s", parse_err)
            # Fallback: treat the entire LLM response as the executive summary
            return {"summary": StructuredSummary(
                executive_summary=content.strip(),
                key_decisions=[],
                open_questions=[],
                risks=[],
                assumptions=[],
                action_items=[],
                stakeholders=[],
                scope=[],
                out_of_scope=[],
            )}

    except Exception as e:
        logger.exception("Summarize node LLM failure")
        return {"summary": StructuredSummary(
            executive_summary=(raw_text[:300] + "...") if raw_text else "",
            key_decisions=[],
            open_questions=[],
            risks=[],
            assumptions=[],
            action_items=[],
            stakeholders=[],
            scope=[],
            out_of_scope=[],
        )}
