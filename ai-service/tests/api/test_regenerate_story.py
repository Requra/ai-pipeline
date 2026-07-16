from __future__ import annotations

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from app.config import settings

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(settings, "AI_INTERNAL_SERVICE_TOKEN", TOKEN)

@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)

def test_regenerate_requires_auth(client):
    resp = client.post("/internal/stories/regenerate", json={
        "requirement_text": "Req",
        "feedback": "Fix AC"
    })
    assert resp.status_code == 401

def test_regenerate_invalid_token(client):
    resp = client.post("/internal/stories/regenerate", headers={"Authorization": "Bearer wrong"}, json={
        "requirement_text": "Req",
        "feedback": "Fix AC"
    })
    assert resp.status_code == 403

def test_regenerate_returns_valid_story(client):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = """
    {
      "title": "Refined Story Title",
      "description": "As a user, I want to authenticate, so that I can log in.",
      "acceptance_criteria": [
        {
          "id": "ac_1",
          "text": "Given a user on the page, when they login, then access is granted.",
          "criterion_type": "Given-When-Then"
        }
      ],
      "labels": ["FR"]
    }
    """
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.api.internal.get_llm", return_value=mock_llm):
        resp = client.post("/internal/stories/regenerate", headers=AUTH, json={
            "requirement_text": "The system shall authenticate users.",
            "requirement_type": "FR",
            "actor": "user",
            "feedback": "Focus on email auth only.",
            "source_context": "SSO is disabled."
        })
        
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Refined Story Title"
        assert "As a user" in data["description"]
        assert len(data["acceptance_criteria"]) == 1
        assert data["acceptance_criteria"][0]["id"] == "ac_1"
        assert data["labels"] == ["FR"]

def test_regenerate_no_llm_returns_503(client):
    with patch("app.api.internal.get_llm", return_value=None):
        resp = client.post("/internal/stories/regenerate", headers=AUTH, json={
            "requirement_text": "Req",
            "feedback": "Fix AC"
        })
        assert resp.status_code == 503
        assert "LLM reasoning service not initialized" in resp.json()["detail"]

def test_regenerate_timeout_returns_504(client):
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())

    with patch("app.api.internal.get_llm", return_value=mock_llm):
        resp = client.post("/internal/stories/regenerate", headers=AUTH, json={
            "requirement_text": "Req",
            "feedback": "Fix AC"
        })
        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]

def test_regenerate_invalid_json_returns_502(client):
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Not a JSON"
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.api.internal.get_llm", return_value=mock_llm):
        resp = client.post("/internal/stories/regenerate", headers=AUTH, json={
            "requirement_text": "Req",
            "feedback": "Fix AC"
        })
        assert resp.status_code == 502
        assert "parsing or validation failed" in resp.json()["detail"]
