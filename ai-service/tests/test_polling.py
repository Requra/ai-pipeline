import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch
from app.main import app
from app.progress import progress_store, update_progress

def test_cors_headers_present():
    client = TestClient(app)
    # Test CORS preflight on GET /status
    response = client.options("/status/some-job", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # Test CORS preflight on POST /process-json
    response = client.options("/process-json", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

def test_status_endpoint_returns_404_if_not_found():
    client = TestClient(app)
    resp = client.get("/status/non-existent-job-id")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Job not found"

@pytest.mark.asyncio
async def test_process_json_async_polling_flow():
    client = TestClient(app)
    
    # Mock pipeline execution so it doesn't hit external APIs
    mock_pipeline = MagicMock()
    mock_pipeline.ainvoke = AsyncMock(return_value={
        "status": "success",
        "job_result": {"dummy": "result"}
    })
    
    with patch("app.main.pipeline", mock_pipeline):
        payload = {
            "job_id": "test-polling-job-1",
            "content": "This is a software requirement description.",
            "source_type": "text",
            "metadata": {}
        }
        
        # Call process endpoint
        resp = client.post("/process-json", json=payload)
        assert resp.status_code == 202
        assert resp.json() == {
            "job_id": "test-polling-job-1",
            "status": "QUEUED"
        }
        
        # TestClient runs background tasks synchronously.
        # Let's inspect the progress_store and the GET /status endpoint.
        status_resp = client.get("/status/test-polling-job-1")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["status"] == "COMPLETED"
        assert data["progress_pct"] == 100
        assert data["current_node"] == "format"
        assert data["result"] == {"dummy": "result"}
