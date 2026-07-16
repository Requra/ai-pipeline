"""Full LangGraph regression tests for grouped document fixtures.

These tests execute the real graph and nodes while replacing provider calls with
deterministic responses. The live HTTP runner in scripts/run_fixture_uploads.py
is required to validate a real configured model provider.
"""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.graph.pipeline import build_pipeline


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evaluate_pipeline as evaluation  # noqa: E402


FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "test-fixtures"


def _bundle(group: str) -> str:
    files = sorted((FIXTURE_ROOT / group).glob("*.txt"))
    return "\n\n".join(
        f"===== SOURCE DOCUMENT: {path.name} =====\n{path.read_text(encoding='utf-8').strip()}"
        for path in files
    )


def _fixture_llm(*, conflict: bool) -> MagicMock:
    base = evaluation.make_mock_llm()
    base_call = base.ainvoke.side_effect

    async def ainvoke(messages, **kwargs):
        user = messages[1][1] if len(messages) > 1 else ""
        if conflict and "Candidate Pairs to Analyze:" in user:
            return MagicMock(content=json.dumps([
                {
                    "requirement_a": "REQ-001",
                    "requirement_b": "REQ-002",
                    "classification": "CONTRADICTION",
                    "confidence": 0.98,
                    "reason": "The policies disagree about who may approve a password reset.",
                    "clarification_question": "Should password reset be self-service or administrator-approved?",
                }
            ]))
        return await base_call(messages, **kwargs)

    base.ainvoke.side_effect = ainvoke
    return base


async def _run_group(group: str, *, conflict: bool):
    pipeline = build_pipeline()
    state = evaluation._initial_state(f"fixture-{group}", _bundle(group))
    state["metadata"] = {"fixture_group": group}
    llm = _fixture_llm(conflict=conflict)

    with ExitStack() as stack:
        stack.enter_context(patch.object(settings, "ENABLE_CONFLICT_DETECTION", conflict))
        stack.enter_context(patch("app.llm.get_llm", return_value=llm))
        for node in evaluation._NODES_WITH_LLM:
            stack.enter_context(patch(f"app.nodes.{node}.get_llm", return_value=llm))
        stack.enter_context(patch("app.nodes.dedupe_requirements.get_llm", return_value=llm))
        result = await pipeline.ainvoke(state)
    return result["job_result"]


@pytest.mark.asyncio
async def test_complementary_fixture_group_covers_pipeline_contracts():
    job_result = await _run_group("complementary", conflict=False)
    metrics = evaluation.evaluate(job_result)

    assert job_result.status in {"completed", "partial"}
    assert metrics["requirement_count"] >= 5
    assert metrics["story_count"] > 0
    assert metrics["traceability_coverage"] == 1.0
    assert metrics["source_refs_coverage"] >= 0.9
    assert metrics["all_stories_have_2_acs"] is True
    assert metrics["exports_available"] is True


@pytest.mark.asyncio
async def test_conflicting_fixture_group_surfaces_semantic_conflict():
    job_result = await _run_group("conflicts", conflict=True)
    warning_codes = {warning.code for warning in job_result.warnings}
    issue_rules = {issue.rule_violated for issue in job_result.quality_issues}

    assert job_result.status in {"completed", "partial"}
    assert "SEMANTIC_CONTRADICTION" in warning_codes
    assert "semantic_conflict_contradiction" in issue_rules
    assert job_result.requirements
    assert job_result.user_stories
