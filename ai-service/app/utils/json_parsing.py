"""
Tolerant JSON parsing for LLM output.

LLMs frequently wrap JSON in ``` fences, add a sentence of prose before/after,
or emit a bare list instead of the expected object. These helpers recover from
those cases deterministically, and offer a single optional LLM "repair" round
for genuinely malformed output — so one bad chunk never crashes the whole job.

Nothing here logs raw content; callers decide what (if anything) to log.
"""

from __future__ import annotations

import json
from typing import Any, Optional

_OPEN_TO_CLOSE = {"{": "}", "[": "]"}

DEFAULT_REPAIR_INSTRUCTION = (
    "You are a strict JSON repair function. The user message contains text that "
    "was supposed to be a single valid JSON value but is malformed. Return ONLY "
    "the corrected JSON value. No markdown, no code fences, no commentary."
)


def strip_code_fences(text: str) -> str:
    """Remove a leading/trailing markdown code fence if present."""
    if not text:
        return ""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def extract_json_span(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` or ``[...]`` block in ``text``.

    String-aware (ignores brackets inside quoted strings, honours escapes).
    Returns None when no balanced block is found.
    """
    if not text:
        return None

    start = -1
    opener = ""
    for i, ch in enumerate(text):
        if ch in _OPEN_TO_CLOSE:
            start = i
            opener = ch
            break
    if start == -1:
        return None

    closer = _OPEN_TO_CLOSE[opener]
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    return None


def loads_loose(text: str) -> Any:
    """Parse JSON from possibly-noisy LLM text.

    Strategy: strip fences → ``json.loads`` → fall back to the first balanced
    JSON span. Raises ``ValueError``/``json.JSONDecodeError`` if all fail.
    """
    cleaned = strip_code_fences(text or "")
    if not cleaned:
        raise ValueError("empty content; nothing to parse")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        span = extract_json_span(cleaned)
        if span is None:
            raise
        return json.loads(span)  # may raise — caller handles


async def _llm_repair(llm, bad_text: str, instruction: str) -> str:
    raw = await llm.ainvoke([("system", instruction), ("user", bad_text or "")])
    return getattr(raw, "content", None) or str(raw)


async def loads_with_llm_repair(
    text: str,
    llm: Any = None,
    *,
    instruction: str = DEFAULT_REPAIR_INSTRUCTION,
) -> Any:
    """Parse JSON, attempting exactly one LLM repair round on failure.

    With no ``llm`` this is equivalent to :func:`loads_loose`. The repair is
    best-effort: if the repaired output still doesn't parse, the original parse
    error is raised so the caller can degrade gracefully.
    """
    try:
        return loads_loose(text)
    except (ValueError, json.JSONDecodeError):
        if llm is None:
            raise
        repaired = await _llm_repair(llm, text or "", instruction)
        return loads_loose(repaired)
