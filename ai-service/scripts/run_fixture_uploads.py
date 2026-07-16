"""Upload grouped pipeline fixtures to a running AI service and validate results.

Usage from ai-service/:
    poetry run python scripts/run_fixture_uploads.py --group complementary
    poetry run python scripts/run_fixture_uploads.py --group conflicts

This is an intentionally real HTTP test. It uses the configured provider and
does not replace the model with a mock. The deterministic counterpart is
tests/test_fixture_document_groups.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = ROOT / "test-fixtures"
GROUPS = ("complementary", "conflicts")
TERMINAL = {"COMPLETED", "PARTIAL", "REJECTED", "FAILED", "CANCELLED"}


def bundle_group(group: str) -> str:
    directory = FIXTURE_ROOT / group
    if not directory.is_dir():
        raise ValueError(f"Unknown fixture group: {group}")
    files = sorted(directory.glob("*.txt"))
    if not files:
        raise ValueError(f"Fixture group has no .txt documents: {directory}")
    return "\n\n".join(
        f"===== SOURCE DOCUMENT: {path.name} =====\n{path.read_text(encoding='utf-8').strip()}"
        for path in files
    )


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _validate_result(group: str, result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    status = str(result.get("status", "")).lower()
    requirements = result.get("requirements") or []
    stories = result.get("user_stories") or []
    warnings = result.get("warnings") or []
    quality_issues = result.get("quality_issues") or []

    if status in {"failed", "rejected"}:
        failures.append(f"result status is {status}")
    if not requirements:
        failures.append("no requirements were extracted")
    if not stories:
        failures.append("no user stories were generated")

    missing_story_links = [
        story for story in stories
        if not story.get("requirement_id") and not story.get("source_requirement_ids")
    ]
    if missing_story_links:
        failures.append(f"{len(missing_story_links)} user stories have no requirement link")

    missing_acceptance = [
        story for story in stories
        if len(story.get("acceptance_criteria") or []) < 2
    ]
    if missing_acceptance:
        failures.append(f"{len(missing_acceptance)} user stories have fewer than two acceptance criteria")

    missing_refs = [req for req in requirements if not req.get("source_refs")]
    if missing_refs:
        failures.append(f"{len(missing_refs)} requirements have no source references")

    if group == "conflicts":
        semantic_warning = any(str(w.get("code", "")).startswith("SEMANTIC_") for w in warnings)
        semantic_issue = any(str(i.get("rule_violated", "")).startswith("semantic_conflict_") for i in quality_issues)
        if not semantic_warning or not semantic_issue:
            failures.append(
                "conflict signal missing; enable ENABLE_CONFLICT_DETECTION=true and rerun"
            )

    return failures


def run_group(client: httpx.Client, base_url: str, token: str, group: str, poll_seconds: float, timeout_seconds: int) -> dict[str, Any]:
    job_id = f"fixture-{group}-{uuid.uuid4().hex[:10]}"
    headers = {"Authorization": f"Bearer {token}", "X-Request-Id": job_id}
    bundle = bundle_group(group)
    files = {"file": (f"{group}-fixture.txt", bundle.encode("utf-8"), "text/plain")}
    data = {
        "job_id": job_id,
        "project_id": "fixture-validation",
        "tenant_id": "fixture-tenant",
        "document_id": f"{job_id}-source",
    }

    submit = client.post(f"{base_url}/internal/process", headers=headers, data=data, files=files)
    submit_data = _json(submit)
    submit.raise_for_status()
    job_id = submit_data.get("job_id", job_id)

    deadline = time.monotonic() + timeout_seconds
    status_data: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status_response = client.get(f"{base_url}/internal/jobs/{job_id}", headers=headers)
        status_response.raise_for_status()
        status_data = _json(status_response)
        if status_data.get("status") in TERMINAL:
            break
        time.sleep(poll_seconds)
    else:
        return {"group": group, "job_id": job_id, "status": "TIMEOUT", "failures": ["polling timeout"]}

    result: dict[str, Any] = {}
    if status_data.get("status") in {"COMPLETED", "PARTIAL", "REJECTED"}:
        result_response = client.get(f"{base_url}/internal/jobs/{job_id}/result", headers=headers)
        result_response.raise_for_status()
        result = _json(result_response)

    failures = _validate_result(group, result) if result else [status_data.get("error") or "no result returned"]
    return {
        "group": group,
        "job_id": job_id,
        "status": status_data.get("status"),
        "current_node": status_data.get("current_node"),
        "progress_pct": status_data.get("progress_pct"),
        "warning_count": status_data.get("warning_count"),
        "result_status": result.get("status"),
        "requirement_count": len(result.get("requirements") or []),
        "story_count": len(result.get("user_stories") or []),
        "warnings": result.get("warnings") or [],
        "quality_issues": result.get("quality_issues") or [],
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real HTTP uploads for grouped pipeline fixtures.")
    parser.add_argument("--group", choices=GROUPS + ("all",), default="all")
    parser.add_argument("--base-url", default=os.getenv("AI_SERVICE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.getenv("AI_INTERNAL_SERVICE_TOKEN"))
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    if not args.token:
        parser.error("--token or AI_INTERNAL_SERVICE_TOKEN is required")

    groups = GROUPS if args.group == "all" else (args.group,)
    base_url = args.base_url.rstrip("/")
    reports = []
    with httpx.Client(timeout=60.0) as client:
        for group in groups:
            try:
                reports.append(run_group(client, base_url, args.token, group, args.poll_seconds, args.timeout_seconds))
            except Exception as exc:
                reports.append({"group": group, "status": "ERROR", "failures": [f"{type(exc).__name__}: {exc}"]})

    print(json.dumps(reports, indent=2, ensure_ascii=False))
    return 1 if any(report.get("failures") for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
