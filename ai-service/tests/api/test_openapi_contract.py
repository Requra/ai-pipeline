import json
import os
import pytest
from app.main import app


def test_openapi_schema_drift_detection():
    """Verify that committed docs/openapi.json matches the live FastAPI OpenAPI schema."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Walk up until we find repo root containing docs/openapi.json
    cursor = current_dir
    schema_path = None
    for _ in range(5):
        candidate = os.path.join(cursor, "docs", "openapi.json")
        if os.path.exists(candidate):
            schema_path = candidate
            break
        cursor = os.path.dirname(cursor)

    assert schema_path is not None and os.path.exists(schema_path), f"Committed openapi.json missing in workspace docs directory"

    with open(schema_path, "r", encoding="utf-8") as f:
        committed_schema = json.load(f)

    live_schema = app.openapi()

    # Core endpoints must exist in both
    required_paths = [
        "/internal/process",
        "/internal/jobs",
        "/internal/jobs/{job_id}",
        "/internal/jobs/{job_id}/result",
        "/ready",
        "/health",
    ]

    for path in required_paths:
        assert path in live_schema["paths"], f"Endpoint {path} missing in live API schema"
        assert path in committed_schema["paths"], f"Endpoint {path} missing in committed openapi.json"

    # Live and committed path keys must match exactly
    assert set(live_schema["paths"].keys()) == set(committed_schema["paths"].keys())
