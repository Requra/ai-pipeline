"""Phase 3 — tolerant JSON parsing + one-shot LLM repair."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.utils.json_parsing import (
    extract_json_span,
    loads_loose,
    loads_with_llm_repair,
    strip_code_fences,
)


def test_strip_code_fences_plain_and_fenced():
    assert strip_code_fences('{"a":1}') == '{"a":1}'
    fenced = "```json\n{\"a\": 1}\n```"
    assert strip_code_fences(fenced) == '{"a": 1}'


def test_loads_loose_direct_object():
    assert loads_loose('{"a": 1}') == {"a": 1}


def test_loads_loose_fenced_object():
    assert loads_loose("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_loads_loose_list():
    assert loads_loose("[1, 2, 3]") == [1, 2, 3]


def test_loads_loose_with_prose_around_json():
    text = 'Sure, here you go:\n{"requirements": [{"id": 1}]}\nHope that helps!'
    assert loads_loose(text) == {"requirements": [{"id": 1}]}


def test_extract_json_span_ignores_brackets_in_strings():
    text = 'prefix {"q": "a } b ] c"} suffix'
    span = extract_json_span(text)
    assert span == '{"q": "a } b ] c"}'
    assert json.loads(span) == {"q": "a } b ] c"}


def test_loads_loose_raises_on_garbage():
    with pytest.raises((ValueError, json.JSONDecodeError)):
        loads_loose("this is not json at all")


def test_loads_loose_empty_raises():
    with pytest.raises(ValueError):
        loads_loose("")


@pytest.mark.asyncio
async def test_repair_not_invoked_when_valid():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="SHOULD_NOT_BE_CALLED"))
    result = await loads_with_llm_repair('{"ok": true}', llm)
    assert result == {"ok": True}
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_repair_invoked_once_on_malformed():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"fixed": true}'))
    # Unbalanced/garbage input forces exactly one repair round.
    result = await loads_with_llm_repair("oops { not valid", llm)
    assert result == {"fixed": True}
    assert llm.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_repair_without_llm_reraises():
    with pytest.raises((ValueError, json.JSONDecodeError)):
        await loads_with_llm_repair("still not json", llm=None)
