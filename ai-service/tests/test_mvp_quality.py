"""
Phase 9 — MVP quality regression tests.

Runs the full pipeline over the fixtures with the deterministic mock LLM (no API
keys, no cost) and asserts the MVP thresholds. Reuses the evaluation harness so
the script and the tests cannot drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evaluate_pipeline as ev  # noqa: E402
from app.config import (  # noqa: E402
    MVP_ENABLE_CONFLICT_DETECTION_DEFAULT,
    MVP_ENABLE_QUALITY_REPAIR_DEFAULT,
)


def _read(name: str) -> str:
    return (ev.FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_mvp_conflict_detection_and_quality_repair_default_to_enabled():
    assert MVP_ENABLE_CONFLICT_DETECTION_DEFAULT == "true"
    assert MVP_ENABLE_QUALITY_REPAIR_DEFAULT == "true"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "simple_project_brief.txt",
    "meeting_transcript.txt",
    "duplicate_requirements.txt",
    "nfr_br_requirements.txt",
])
async def test_relevant_fixture_meets_mvp_thresholds(name):
    jr = await ev.run_fixture(name, _read(name))
    metrics = ev.evaluate(jr)
    failures = ev.check_thresholds(name, relevant=True, metrics=metrics)
    assert not failures, f"{name}: {failures}"
    # Spot-check the core MVP guarantees explicitly.
    assert metrics["requirement_count"] > 0
    assert metrics["story_count"] > 0
    assert metrics["traceability_coverage"] == 1.0
    assert metrics["source_refs_coverage"] >= 0.9
    assert metrics["all_stories_have_acceptance_criteria"] is True
    assert metrics["exports_available"] is True


@pytest.mark.asyncio
async def test_irrelevant_fixture_is_rejected():
    jr = await ev.run_fixture("irrelevant_text.txt", _read("irrelevant_text.txt"))
    metrics = ev.evaluate(jr)
    assert metrics["status"] == "rejected"
    assert metrics["story_count"] == 0
    assert ev.check_thresholds("irrelevant_text.txt", relevant=False, metrics=metrics) == []


@pytest.mark.asyncio
async def test_duplicate_fixture_merges_duplicates():
    jr = await ev.run_fixture("duplicate_requirements.txt", _read("duplicate_requirements.txt"))
    metrics = ev.evaluate(jr)
    # The fixture repeats requirements; dedupe must collapse some.
    assert metrics["duplicates_merged"] >= 1


@pytest.mark.asyncio
async def test_pipeline_does_not_crash_on_malformed_input():
    # Garbage / non-software input must degrade gracefully (no exception).
    jr = await ev.run_fixture("garbage", "!!!??? \n\n   ###   \n\n 12345 6789")
    assert jr is not None
    assert jr.status in {"rejected", "partial", "failed", "completed"}
