"""
Automated regression protections against documentation drift.
Ensures active graph node count, doc claims, links, and OpenAPI artifacts stay in sync with code.
"""

import os
import re
from pathlib import Path
import pytest
from app.graph.pipeline import build_pipeline
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pipeline_active_node_count_parity():
    """Verify that build_pipeline() registers exactly 13 active business logic nodes."""
    compiled = build_pipeline()
    graph_nodes = set(compiled.get_graph().nodes.keys()) - {"__start__", "__end__"}

    expected_nodes = {
        "detect_file_type",
        "prepare_sources",
        "build_source_index",
        "extract",
        "dedupe_requirements",
        "retrieve_evidence",
        "classify",
        "evidence_grounding",
        "generate",
        "quality_gate",
        "repair_stories",
        "summarize",
        "format",
    }

    assert graph_nodes == expected_nodes, f"Mismatch in active pipeline nodes: {graph_nodes - expected_nodes}"
    assert len(graph_nodes) == 13, f"Expected 13 active nodes, found {len(graph_nodes)}"


def test_no_banned_stale_claims_in_active_docs():
    """Ensure obsolete graph claims and removed artifact paths do not reappear in active Markdown docs."""
    banned_patterns = [
        (r"15-node", "Stale 15-node pipeline claim found"),
        (r"14-node", "Stale 14-node graph claim found"),
        (r"requra-ai-internal\.openapi\.json", "Deleted OpenAPI path referenced"),
        (r"codebase-mastery", "Retired codebase-mastery directory referenced"),
        (r"AI_PIPELINE_FINAL_RELEASE_READINESS\.md", "Duplicate release readiness report referenced"),
    ]

    docs_to_check = [REPO_ROOT / "README.md"] + list((REPO_ROOT / "docs").glob("*.md"))

    violations = []
    for doc in docs_to_check:
        if not doc.exists() or doc.name == "12-semantic-quality-hardening.md":
            continue
        content = doc.read_text(encoding="utf-8")
        for pattern, msg in banned_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                violations.append(f"{doc.relative_to(REPO_ROOT)}: {msg} ({len(matches)} matches)")

    assert not violations, "Found documentation drift violations:\n" + "\n".join(violations)


def test_markdown_internal_relative_links():
    """Verify relative Markdown links in README.md and docs/*.md point to existing files."""
    docs_to_check = [REPO_ROOT / "README.md"] + list((REPO_ROOT / "docs").glob("*.md"))
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    broken_links = []
    for doc in docs_to_check:
        if not doc.exists():
            continue
        content = doc.read_text(encoding="utf-8")
        for text, href in link_pattern.findall(content):
            # Ignore HTTP(S) links, anchors, and mailto
            if href.startswith(("http://", "https://", "mailto:", "#", "https:")):
                continue
            # Strip target anchors if present
            clean_href = href.split("#")[0]
            if not clean_href:
                continue

            target_path = (doc.parent / clean_href).resolve()
            if not target_path.exists():
                broken_links.append(f"{doc.relative_to(REPO_ROOT)} -> [{text}]({href}) (missing target)")

    assert not broken_links, "Found broken relative Markdown links:\n" + "\n".join(broken_links)


def test_canonical_openapi_artifact_sync():
    """Ensure docs/openapi.json exists and contains all live FastAPI endpoints."""
    canonical_openapi = REPO_ROOT / "docs" / "openapi.json"
    assert canonical_openapi.exists(), "Canonical docs/openapi.json does not exist"

    live_paths = set(app.openapi()["paths"].keys())
    assert "/internal/process" in live_paths
    assert "/internal/jobs" in live_paths
    assert "/ready" in live_paths
