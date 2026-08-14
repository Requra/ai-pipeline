"""Tests for lazy embedding and semantic conflict detection in dedupe_requirements."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.nodes.dedupe_requirements import dedupe_requirements_node
from app.rag import embeddings as emb
from app.rag.requirement_embeddings import RequirementEmbeddingService
from app.schemas.items import EvidenceSpan, ExtractedRequirement
from app.config import settings

class _FakeEmbedder:
    model = "fake-model"

    async def embed_documents(self, texts):
        return [[0.5] * 24 for _ in texts]

    async def embed_query(self, text):
        return [0.5] * 24


def _req(rid, text, *, embedding=None):
    return ExtractedRequirement(
        id=rid,
        text=text,
        confidence=0.8,
        evidence=[EvidenceSpan(chunk_id=f"c{rid}", quote=text[:10])],
        embedding=embedding,
    )


def _state(reqs):
    return {
        "job_id": "test-job",
        "extracted_requirements": reqs,
        "warnings": [],
        "quality_issues": [],
    }


@pytest.mark.asyncio
async def test_lazy_embedding_service():
    emb.set_embedder(_FakeEmbedder())
    try:
        reqs = [
            _req(1, "Req one", embedding=[0.9] * 24),
            _req(2, "Req two", embedding=None),
        ]
        await RequirementEmbeddingService.ensure_requirement_embeddings(reqs)
        assert reqs[0].embedding == [0.9] * 24  # untouched
        assert reqs[1].embedding == [0.5] * 24  # newly embedded
    finally:
        emb.set_embedder(None)


@pytest.mark.asyncio
async def test_conflict_detection_with_embeddings():
    emb.set_embedder(_FakeEmbedder())
    
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = """
    [
      {
        "requirement_a": "REQ-001",
        "requirement_b": "REQ-002",
        "classification": "CONTRADICTION",
        "confidence": 0.95,
        "reason": "They contradict each other directly.",
        "clarification_question": "Which is correct?"
      }
    ]
    """
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    reqs = [
        _req(1, "The admin can delete users.", embedding=[0.1] * 24),
        _req(2, "Only managers can delete users.", embedding=[0.1] * 24),
    ]
    state = _state(reqs)
    
    with patch.object(settings, "ENABLE_CONFLICT_DETECTION", True), \
         patch.object(settings, "ENABLE_EMBEDDINGS", True), \
         patch("app.nodes.dedupe_requirements.get_llm", return_value=mock_llm):
        try:
            out = await dedupe_requirements_node(state)
            
            # Assert warnings & quality issues are produced
            warnings = out.get("warnings", [])
            issues = out.get("quality_issues", [])
            
            assert any(w.code == "SEMANTIC_CONTRADICTION" for w in warnings)
            assert any("semantic_conflict_contradiction" in issue.rule_violated for issue in issues)
            assert any("REQ-002" in w.message for w in warnings)
        finally:
            emb.set_embedder(None)


@pytest.mark.asyncio
async def test_conflict_detection_jaccard_fallback():
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = """
    [
      {
        "requirement_a": "REQ-001",
        "requirement_b": "REQ-002",
        "classification": "CONSTRAINT_CONFLICT",
        "confidence": 0.85,
        "reason": "Constraints overlap incorrectly.",
        "clarification_question": "What is the correct constraint?"
      }
    ]
    """
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    # Text similarity in Jaccard range (0.3 - 0.8)
    reqs = [
        _req(1, "The system dashboard page shall load in less than one second."),
        _req(2, "The main system page dashboard shall load under five seconds."),
    ]
    state = _state(reqs)
    
    with patch.object(settings, "ENABLE_CONFLICT_DETECTION", True), \
         patch.object(settings, "ENABLE_EMBEDDINGS", False), \
         patch("app.nodes.dedupe_requirements.get_llm", return_value=mock_llm):
        
        out = await dedupe_requirements_node(state)
        
        warnings = out.get("warnings", [])
        issues = out.get("quality_issues", [])
        
        assert any(w.code == "SEMANTIC_CONSTRAINT_CONFLICT" for w in warnings)
        assert any("semantic_conflict_constraint_conflict" in issue.rule_violated for issue in issues)


@pytest.mark.asyncio
async def test_complementary_relationship_is_informational_not_a_defect():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="""
    [
      {
        "requirement_a": "REQ-001",
        "requirement_b": "REQ-002",
        "classification": "COMPLEMENTARY",
        "confidence": 0.95,
        "reason": "The second rule constrains the first workflow.",
        "clarification_question": "None",
        "resolution_options": ["Revise both requirements."]
      }
    ]
    """))
    reqs = [
        _req(1, "The system dashboard page shall load in less than one second."),
        _req(2, "The main system page dashboard shall load under five seconds."),
    ]
    state = _state(reqs)

    with patch.object(settings, "ENABLE_CONFLICT_DETECTION", True), \
         patch.object(settings, "ENABLE_EMBEDDINGS", False), \
         patch("app.nodes.dedupe_requirements.get_llm", return_value=mock_llm):
        out = await dedupe_requirements_node(state)

    warning = next(w for w in out["warnings"] if w.code == "SEMANTIC_COMPLEMENTARY")
    assert warning.message.startswith("Related requirements")
    assert "Conflict detected" not in warning.message
    assert "Proposed Resolutions" not in warning.message
    assert not out.get("quality_issues")


@pytest.mark.asyncio
async def test_orthogonal_numeric_constraints_are_not_published_as_conflict():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="""
    [
      {
        "requirement_a": "REQ-001",
        "requirement_b": "REQ-002",
        "classification": "CONSTRAINT_CONFLICT",
        "confidence": 0.95,
        "reason": "Both constrain checkout.",
        "clarification_question": "Which constraint wins?",
        "resolution_options": ["Change one requirement."]
      }
    ]
    """))
    reqs = [
        _req(1, "Checkout requests require manager approval when asset value exceeds $1,000."),
        _req(2, "Standard users may check out up to 3 assets simultaneously."),
    ]
    state = _state(reqs)

    with patch.object(settings, "ENABLE_CONFLICT_DETECTION", True), \
         patch.object(settings, "ENABLE_EMBEDDINGS", False), \
         patch(
             "app.nodes.dedupe_requirements._find_jaccard_candidates",
             return_value=[(reqs[0], reqs[1], 0.5)],
         ), \
         patch("app.nodes.dedupe_requirements.get_llm", return_value=mock_llm):
        out = await dedupe_requirements_node(state)

    assert any(w.code == "SEMANTIC_COMPLEMENTARY" for w in out["warnings"])
    assert not any(w.code == "SEMANTIC_CONSTRAINT_CONFLICT" for w in out["warnings"])
    assert not out.get("quality_issues")
