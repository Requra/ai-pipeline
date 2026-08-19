import pytest
import json
import httpx
from unittest.mock import patch, MagicMock, AsyncMock
from app.graph.pipeline import build_pipeline
from app.schemas.items import JobResult
from app.config import settings

def mocked_iti_post(url, json_data, headers):
    model_id = json_data.get("model_id")
    
    # 1. Check if it's an embedding request
    if url.endswith("/student/embed"):
        # Return 1536-dimensional mock vector
        return MagicMock(
            status_code=200,
            json=lambda: {"embedding": [[0.01] * 1536] * len(json_data.get("texts", []))}
        )
        
    # 2. Otherwise it's a chat request
    messages = json_data.get("messages", [])
    prompt_text = messages[0].get("content", "") if messages else ""
    system_prompt = json_data.get("system_prompt", "")
    
    # Ingest node relevance check
    if "strict software-document gatekeeper" in system_prompt or "strict software-document gatekeeper" in prompt_text:
        return MagicMock(
            status_code=200,
            json=lambda: {
                "output_text": json.dumps({
                    "is_useful": True,
                    "relevance_score": 1.0,
                    "reason": "mocked relevance success"
                }),
                "model_id": model_id,
                "status": "completed"
            }
        )
        
    # Extract node
    if "Extract atomic software requirements" in system_prompt or "Extract requirements" in prompt_text:
        return MagicMock(
            status_code=200,
            json=lambda: {
                "output_text": json.dumps({
                    "requirements": [
                        {
                            "id": 1,
                            "text": "The system shall process payments securely.",
                            "actor": "System",
                            "goal": "process payments securely",
                            "candidate_labels": ["FR"],
                            "confidence": 0.95,
                            "evidence": [{"chunk_id": "c1", "quote": "process payments"}]
                        }
                    ]
                }),
                "model_id": model_id,
                "status": "completed"
            }
        )
        
    # Classify node
    if "You classify each requirement" in system_prompt:
        return MagicMock(
            status_code=200,
            json=lambda: {
                "output_text": json.dumps({
                    "classifications": [
                        {"id": 1, "labels": ["FR"], "confidence": 0.95}
                    ]
                }),
                "model_id": model_id,
                "status": "completed"
            }
        )
        
    # Generate node
    if "Convert requirements into USER STORIES" in system_prompt:
        return MagicMock(
            status_code=200,
            json=lambda: {
                "output_text": json.dumps({
                    "stories": [
                        {
                            "source_requirement_id": 1,
                            "title": "Secure Payment Processing",
                            "description": "As a user, I want secure payment processing so that my credit card details are safe.",
                            "acceptance_criteria": [
                                "Given a user with card details, when they pay, then transaction is processed securely."
                            ],
                            "labels": ["FR"]
                        }
                    ]
                }),
                "model_id": model_id,
                "status": "completed"
            }
        )
        
    # Summarize node
    if "expert business analyst" in system_prompt:
        return MagicMock(
            status_code=200,
            json=lambda: {
                "output_text": "Mocked executive summary for ITI.",
                "model_id": model_id,
                "status": "completed"
            }
        )
        
    return MagicMock(
        status_code=200,
        json=lambda: {
            "output_text": "{}",
            "model_id": model_id,
            "status": "completed"
        }
    )

@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_iti_pipeline_end_to_end_mocked(mock_async_client_class, monkeypatch):
    # Configure pipeline to use ITI for both chat and embeddings
    monkeypatch.setattr(settings, "LLM_PROVIDER", "iti")
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "iti")
    monkeypatch.setattr(settings, "ENABLE_EMBEDDINGS", True)
    monkeypatch.setattr(settings, "ENABLE_HYBRID_RETRIEVAL", True)
    monkeypatch.setattr(settings, "ITI_API_KEY", "mock-sbg-key")
    monkeypatch.setattr(settings, "ITI_BASE_URL", "http://apiaccess.iti.net.eg/student")
    
    mock_client = MagicMock()
    mock_async_client_class.return_value.__aenter__.return_value = mock_client
    
    # Mock post requests to return standard ITI payload structure
    async def mock_post(url, json, headers, **kwargs):
        res = mocked_iti_post(url, json, headers)
        # We need to simulate standard httpx async response returning status_code and json
        async_response = MagicMock()
        async_response.status_code = res.status_code
        async_response.json = MagicMock(return_value=res.json())
        async_response.raise_for_status = MagicMock()
        return async_response
        
    mock_client.post = AsyncMock(side_effect=mock_post)
    
    pipeline = build_pipeline()
    
    initial_state = {
        "job_id": "iti-e2e-json-1",
        "raw_bytes": b"",
        "raw_text": "The system shall process payments securely.",
        "file_type": "text",
        "metadata": {},
        "source_metadata": None,
        "chunks": [],
        "extracted_requirements": [],
        "classified_requirements": [],
        "requirement_coverages": [],
        "user_stories": [],
        "quality_issues": [],
        "warnings": [],
        "export_rows": [],
        "summary": None,
        "is_useful": True,
        "relevance_score": 1.0,
        "status": "started",
        "error": None,
        "started_at": 0,
        "processing_time_ms": 0,
        "functional_requirements": [],
        "enable_embeddings": True,
        "enable_hybrid_retrieval": True
    }
    
    result = await pipeline.ainvoke(initial_state)
    
    assert "job_result" in result
    jr = result["job_result"]
    assert isinstance(jr, JobResult)
    assert jr.status == "completed"
    assert len(jr.requirements) == 1
    assert jr.requirements[0].description == "The system shall process payments securely."
    assert len(jr.user_stories) == 1
    assert jr.user_stories[0].title == "Secure Payment Processing"
    assert jr.summary.executive_summary == "Mocked executive summary for ITI."
    
    # Verify that hybrid embedding index was created and search was executed
    # build_source_index and retrieve_evidence must have completed
    assert "source_index_id" in result
    # In hybrid mode retrieval stats should contain hybrid info
    assert result.get("retrieval_stats", {}).get("mode") == "hybrid"
