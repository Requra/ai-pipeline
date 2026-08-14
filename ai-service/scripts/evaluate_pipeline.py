"""
MVP evaluation harness for the Requra AI pipeline.

Runs the full LangGraph pipeline over the regression fixtures and reports
quality metrics against MVP thresholds. Deterministic by default (a built-in
mock LLM that produces grounded, verbatim-quote output from arbitrary text), so
it runs in CI with no API keys and no cost. Pass ``--real`` to use the
configured provider instead.

Usage (from ai-service/):
    poetry run python scripts/evaluate_pipeline.py
    poetry run python scripts/evaluate_pipeline.py --real
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

# Make `app` importable when run as a plain script.
_AI_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_AI_SERVICE_ROOT))

from app.graph.pipeline import build_pipeline  # noqa: E402

FIXTURES_DIR = _AI_SERVICE_ROOT / "tests" / "fixtures"

# Fixtures and whether they are expected to be accepted (relevant) or rejected.
FIXTURES = {
    "simple_project_brief.txt": {"relevant": True},
    "meeting_transcript.txt": {"relevant": True},
    "duplicate_requirements.txt": {"relevant": True},
    "nfr_br_requirements.txt": {"relevant": True},
    "irrelevant_text.txt": {"relevant": False},
}

_NODES_WITH_LLM = ("ingest", "extract", "classify", "generate", "summarize")

_SOFTWARE_KEYWORDS = (
    "system", "user", "requirement", "shall", "must", "api", "data", "login",
    "password", "support", "ticket", "payment", "encrypt", "service", "platform",
    "dashboard", "account", "admin", "export", "order",
)
_REQUIREMENT_CUES = ("shall", "must", "should", "able to", "allow", "support", "can ", "will ", "need")
_NFR_CUES = ("second", "performance", "available", "uptime", "scale", "concurrent",
             "encrypt", "percentile", " load", "response time", "99", "peak")
_BR_CUES = ("only ", "must not", "policy", "discount", "minimum", "refund",
            "allowed to", "loyalty", "approved", "per company")


# ---------------------------------------------------------------------------
# Deterministic mock LLM
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _infer_label(sentence: str) -> str:
    low = sentence.lower()
    if any(k in low for k in _BR_CUES):
        return "BR"
    if any(k in low for k in _NFR_CUES):
        return "NFR"
    return "FR"


def _payload_after(prefix_text: str, user: str) -> str:
    idx = user.find("\n\n")
    return user[idx + 2:] if idx != -1 else user


def _mock_extract(user: str) -> Dict[str, Any]:
    text = _payload_after("Extract", user)
    reqs = []
    for sentence in _split_sentences(text):
        low = sentence.lower()
        if not any(cue in low for cue in _REQUIREMENT_CUES):
            continue
        reqs.append({
            "id": len(reqs) + 1,
            "text": sentence,
            "candidate_labels": [_infer_label(sentence)],
            "confidence": 0.9,
            "extraction_type": "explicit",
            # Verbatim sentence -> grounds exactly against the source chunk.
            "evidence": [{"chunk_id": "source", "quote": sentence}],
        })
        if len(reqs) >= 15:
            break
    return {"requirements": reqs}


def _mock_classify(user: str) -> Dict[str, Any]:
    pairs = re.findall(r"id:\s*(\d+)\s*\ntext:\s*(.+)", user)
    return {"classifications": [
        {"id": int(i), "labels": [_infer_label(t)], "confidence": 0.9} for i, t in pairs
    ]}


def _mock_generate(user: str) -> Dict[str, Any]:
    pairs = re.findall(r"id:\s*(\d+)\s*\ntext:\s*(.+)", user)
    stories = []
    for i, t in pairs:
        action = t.strip().rstrip(".")
        stories.append({
            "source_requirement_ids": [int(i)],
            "title": action[:60],
            "description": f"As a user, I want {action[:80]}, so that the goal is met.",
            "acceptance_criteria": [
                f"Given the system is ready, when the action is performed, then it satisfies: {action}.",
                "Given invalid or incomplete input, when the action is attempted, then a clear error is shown and it does not complete.",
            ],
            "labels": [_infer_label(t)],
        })
    return {"stories": stories}


def _mock_summary() -> Dict[str, Any]:
    return {
        "executive_summary": "Deterministic mock summary of the project requirements.",
        "key_decisions": ["Use the documented requirements as the scope baseline."],
        "open_questions": [],
        "risks": ["Some requirements may need quantified acceptance thresholds."],
        "assumptions": ["The provided document reflects current scope."],
        "action_items": ["Review generated stories with stakeholders."],
        "stakeholders": ["Product Owner", "Engineering"],
        "scope": ["Functionality described in the source document."],
        "out_of_scope": [],
    }


def make_mock_llm() -> MagicMock:
    """A deterministic chat-LLM mock that grounds output in the input text."""

    async def ainvoke(messages, **kwargs):
        system = messages[0][1] if messages else ""
        user = messages[1][1] if len(messages) > 1 else ""

        if "gatekeeper" in system or "relevance" in system.lower():
            hits = sum(1 for k in _SOFTWARE_KEYWORDS if k in user.lower())
            useful = hits >= 3
            return MagicMock(content=json.dumps({
                "is_useful": useful,
                "relevance_score": 1.0 if useful else 0.1,
                "reason": "mock relevance",
            }))
        if "Extract atomic software requirements" in system:
            return MagicMock(content=json.dumps(_mock_extract(user)))
        if "You classify each requirement" in system:
            return MagicMock(content=json.dumps(_mock_classify(user)))
        if "Convert requirements into USER STORIES" in system:
            return MagicMock(content=json.dumps(_mock_generate(user)))
        if "expert business analyst" in system:
            return MagicMock(content=json.dumps(_mock_summary()))
        return MagicMock(content="{}")

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ainvoke)
    return llm


# ---------------------------------------------------------------------------
# Run + evaluate
# ---------------------------------------------------------------------------

def _initial_state(job_id: str, raw_text: str) -> Dict[str, Any]:
    return {
        "job_id": job_id, "raw_bytes": b"", "raw_text": raw_text, "file_type": "text",
        "metadata": {}, "source_metadata": None, "chunks": [],
        "extracted_requirements": [], "classified_requirements": [], "requirement_coverages": [],
        "user_stories": [], "quality_issues": [], "warnings": [], "export_rows": [],
        "summary": None, "is_useful": False, "relevance_score": 0.0, "status": "started",
        "error": None, "started_at": time.time(), "processing_time_ms": 0,
        "functional_requirements": [],
    }


async def run_fixture(name: str, raw_text: str, use_real: bool = False):
    pipeline = build_pipeline()
    state = _initial_state(f"eval_{name}", raw_text)

    if use_real:
        result = await pipeline.ainvoke(state)
    else:
        llm = make_mock_llm()
        with ExitStack() as stack:
            stack.enter_context(patch("app.llm.get_llm", return_value=llm))
            for node in _NODES_WITH_LLM:
                stack.enter_context(patch(f"app.nodes.{node}.get_llm", return_value=llm))
            result = await pipeline.ainvoke(state)
    return result.get("job_result")


def evaluate(job_result) -> Dict[str, Any]:
    """Compute MVP metrics from a JobResult."""
    if job_result is None:
        return {"status": "failed", "requirement_count": 0, "story_count": 0}

    reqs = job_result.requirements
    stories = job_result.user_stories
    req_count = len(reqs)
    story_count = len(stories)

    stories_with_src = sum(1 for s in stories if s.requirement_id)
    reqs_with_refs = sum(1 for r in reqs if r.source_refs)
    all_have_acs = all(bool(s.acceptance_criteria) for s in stories) if stories else True
    avg_conf = round(sum(r.confidence_score for r in reqs) / req_count, 3) if req_count else 0.0

    merged = 0
    for w in job_result.warnings:
        code = w.code if hasattr(w, "code") else w.get("code", "")
        msg = w.message if hasattr(w, "message") else w.get("message", "")
        if code == "DUPLICATE_REQUIREMENT_MERGED":
            m = re.search(r"Merged (\d+)", msg or "")
            merged = int(m.group(1)) if m else merged

    qr = job_result.quality_report
    return {
        "status": job_result.status,
        "requirement_count": req_count,
        "story_count": story_count,
        "traceability_coverage": round(stories_with_src / story_count, 3) if story_count else 1.0,
        "source_refs_coverage": round(reqs_with_refs / req_count, 3) if req_count else 1.0,
        "all_stories_have_acceptance_criteria": all_have_acs,
        "avg_confidence": avg_conf,
        "duplicates_merged": merged,
        "exports_available": bool(job_result.exports.excel.available),
        "overall_quality": qr.overall_score if qr else None,
        "processing_time_ms": job_result.processing_time_ms,
    }


def check_thresholds(name: str, relevant: bool, metrics: Dict[str, Any]) -> List[str]:
    """Return a list of MVP threshold failures (empty == pass)."""
    failures: List[str] = []
    if not relevant:
        if metrics.get("status") != "rejected":
            failures.append(f"irrelevant input not rejected (status={metrics.get('status')})")
        return failures

    if metrics["requirement_count"] == 0:
        failures.append("no requirements extracted")
    if metrics["story_count"] == 0:
        failures.append("no user stories generated")
    if metrics["traceability_coverage"] < 1.0:
        failures.append(f"traceability_coverage {metrics['traceability_coverage']} < 1.0")
    if metrics["source_refs_coverage"] < 0.9:
        failures.append(f"source_refs_coverage {metrics['source_refs_coverage']} < 0.9")
    if not metrics["all_stories_have_acceptance_criteria"]:
        failures.append("some stories have no acceptance criteria")
    if metrics["story_count"] > 0 and not metrics["exports_available"]:
        failures.append("stories exist but exports unavailable")
    return failures


async def _amain(use_real: bool) -> int:
    print(f"=== Requra pipeline MVP evaluation ({'REAL LLM' if use_real else 'mock LLM'}) ===\n")
    total_failures = 0
    for name, cfg in FIXTURES.items():
        raw_text = (FIXTURES_DIR / name).read_text(encoding="utf-8")
        try:
            jr = await run_fixture(name, raw_text, use_real=use_real)
            metrics = evaluate(jr)
            failures = check_thresholds(name, cfg["relevant"], metrics)
        except Exception as exc:  # eval must never crash on a fixture
            metrics = {"error": f"{type(exc).__name__}: {exc}"}
            failures = [f"pipeline crashed: {type(exc).__name__}"]

        status = "PASS" if not failures else "FAIL"
        total_failures += len(failures)
        print(f"[{status}] {name}")
        for key, value in metrics.items():
            print(f"        {key}: {value}")
        for failure in failures:
            print(f"        ! {failure}")
        print()

    print("=== RESULT:", "ALL THRESHOLDS MET" if total_failures == 0 else f"{total_failures} FAILURE(S)", "===")
    return 0 if total_failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the AI pipeline against MVP thresholds.")
    parser.add_argument("--real", action="store_true", help="Use the configured LLM provider instead of the mock.")
    args = parser.parse_args()
    return asyncio.run(_amain(use_real=args.real))


if __name__ == "__main__":
    raise SystemExit(main())
