"""Database-backed integration tests for repositories.py fixes."""

from __future__ import annotations

import pytest
from sqlalchemy import select, delete

from app.store.db import models as m
from app.store.db.repositories import PgJobStore, PgResultStore, PgChunkStore, PgEmbeddingStore
from app.store.db.session import Database
from app.store.models import (
    AiJobRecord,
    JobAttemptRecord,
    SourceDocumentRecord,
    SourceChunkRecord,
    ChunkEmbeddingRecord,
    JobStatus,
)

pytestmark = pytest.mark.asyncio

import os
from dotenv import dotenv_values
config = dotenv_values("c:/ITI_GP/src/ai-pipeline/ai-service/.env")
DB_URL = config.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql+asyncpg://ai:ai@localhost:5432/ai_pipeline"


async def test_db_save_result_decomposes_cleanly():
    db = Database(DB_URL)
    result_store = PgResultStore(db)
    job_store = PgJobStore(db)
    
    job_id = "test_run_decomp_01"
    
    # Setup: delete existing job and result just in case
    async with db.session() as s:
        await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
        await s.execute(delete(m.AiJobResult).where(m.AiJobResult.job_id == job_id))
        await s.commit()
        
    try:
        # Create a dummy job first
        await job_store.create_job(
            AiJobRecord(
                job_id=job_id,
                tenant_id="test_tenant",
                project_id="test_project",
            )
        )
        
        result_payload = {
            "job_id": job_id,
            "tenant_id": "test_tenant",
            "project_id": "test_project",
            "status": "completed",
            "requirements": [
                {
                    "id": "REQ-001",
                    "description": "Requirement text",
                    "actor": "User",
                    "type": "Functional",
                    "priority": "High",
                    "confidence_score": 0.95,
                    "deduplication_key": "req_key",
                    "source_refs": [
                        {"chunk_id": "chunk-1", "quote": "source quote", "page": 1, "confidence_score": 1.0}
                    ]
                }
            ],
            "user_stories": [
                {
                    "id": "US-001",
                    "requirement_id": "REQ-001",
                    "title": "Story Title",
                    "user_story": "As a user...",
                    "priority": "High",
                    "type": "feature",
                    "deduplication_key": "story_key",
                    "quality": {"score": 1.0, "issues": []},
                    "acceptance_criteria": [
                        {"id": "AC-001", "text": "Acceptance criterion 1", "criterion_type": "plain"}
                    ]
                }
            ]
        }
        
        # Call save_result - this should run completely without throwing IntegrityError
        record = await result_store.save_result(
            job_id,
            result_payload,
            status="completed",
            processing_time_ms=100
        )
        
        assert record.job_id == job_id
        
        # Query db to verify details were written
        async with db.session() as s:
            req = (await s.execute(select(m.AiRequirement).where(m.AiRequirement.job_id == job_id))).scalar_one()
            assert req.requirement_key == "REQ-001"
            
            story = (await s.execute(select(m.AiUserStory).where(m.AiUserStory.job_id == job_id))).scalar_one()
            assert story.story_key == "US-001"
            
            ac = (await s.execute(select(m.AiAcceptanceCriterion).where(m.AiAcceptanceCriterion.job_id == job_id))).scalar_one()
            assert ac.criterion_key == "AC-001"
            assert ac.user_story_id == story.id
            
    finally:
        # Cleanup
        async with db.session() as s:
            await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
            await s.execute(delete(m.AiJobResult).where(m.AiJobResult.job_id == job_id))
            await s.commit()
        await db.dispose()


async def test_db_attempt_upsert():
    db = Database(DB_URL)
    job_store = PgJobStore(db)
    job_id = "test_run_attempt_upsert"
    
    # Setup
    async with db.session() as s:
        await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
        await s.commit()
        
    try:
        # Create the base job
        await job_store.create_job(AiJobRecord(job_id=job_id))
        
        # First call: PROCESSING status
        await job_store.add_attempt(
            JobAttemptRecord(
                job_id=job_id,
                attempt_number=1,
                status=JobStatus.PROCESSING,
            )
        )
        
        attempts = await job_store.list_attempts(job_id)
        assert len(attempts) == 1
        assert attempts[0].status == JobStatus.PROCESSING
        
        # Second call: COMPLETED status
        await job_store.add_attempt(
            JobAttemptRecord(
                job_id=job_id,
                attempt_number=1,
                status=JobStatus.COMPLETED,
                error_code="NONE",
                error_message="Success",
            )
        )
        
        # Verify we still have exactly 1 attempt row but with the updated fields
        attempts_updated = await job_store.list_attempts(job_id)
        assert len(attempts_updated) == 1
        assert attempts_updated[0].status == JobStatus.COMPLETED
        assert attempts_updated[0].error_message == "Success"
        
    finally:
        # Cleanup
        async with db.session() as s:
            await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
            await s.commit()
        await db.dispose()


async def test_db_chunk_idempotency():
    db = Database(DB_URL)
    chunk_store = PgChunkStore(db)
    job_store = PgJobStore(db)
    job_id = "test_run_chunk_idemp"
    
    # Setup
    async with db.session() as s:
        await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
        await s.commit()
        
    try:
        # Create base job
        await job_store.create_job(AiJobRecord(job_id=job_id))
        
        chunks = [
            SourceChunkRecord(job_id=job_id, chunk_id="chunk-1", chunk_index=0, text="first part"),
            SourceChunkRecord(job_id=job_id, chunk_id="chunk-2", chunk_index=1, text="second part"),
        ]
        
        # Save first time
        await chunk_store.save_chunks(chunks)
        assert len(await chunk_store.get_chunks(job_id)) == 2
        
        # Save second time
        await chunk_store.save_chunks(chunks)
        
        # Verify exactly 2 chunks exist
        assert len(await chunk_store.get_chunks(job_id)) == 2
        
    finally:
        # Cleanup
        async with db.session() as s:
            await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
            await s.commit()
        await db.dispose()


async def test_db_embedding_cleanup_on_retry():
    db = Database(DB_URL)
    job_store = PgJobStore(db)
    chunk_store = PgChunkStore(db)
    emb_store = PgEmbeddingStore(db)
    job_id = "test_run_emb_retry"
    
    # Setup
    async with db.session() as s:
        await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
        await s.commit()
        
    try:
        await job_store.create_job(AiJobRecord(job_id=job_id))
        
        # Insert mock chunk first
        await chunk_store.save_chunks([
            SourceChunkRecord(job_id=job_id, chunk_id="c1", chunk_index=0, text="content")
        ])
        
        first_embs = [
            ChunkEmbeddingRecord(job_id=job_id, chunk_id="c1", embedding_model="model-1", embedding=[0.1] * 1536),
            ChunkEmbeddingRecord(job_id=job_id, chunk_id="c2", embedding_model="model-1", embedding=[0.3] * 1536),
        ]
        
        # Save embeddings first time
        await emb_store.save_embeddings(first_embs)
        assert await emb_store.count_for_job(job_id) == 2
        
        # Second execution (retry)
        second_embs = [
            ChunkEmbeddingRecord(job_id=job_id, chunk_id="c1", embedding_model="model-1", embedding=[0.9] * 1536)
        ]
        await emb_store.save_embeddings(second_embs)
        
        # Verify count is 1 (the old ones were cleared)
        assert await emb_store.count_for_job(job_id) == 1
        
    finally:
        # Cleanup
        async with db.session() as s:
            await s.execute(delete(m.AiJob).where(m.AiJob.job_id == job_id))
            await s.commit()
        await db.dispose()
