"""
SQLAlchemy ORM models for the durable AI processing schema (PostgreSQL).

This module is only imported when a ``DATABASE_URL`` is configured, so the
default in-memory path never needs SQLAlchemy's Postgres dialect, asyncpg, or
pgvector installed.

Alembic (``migrations/``) is the source of truth for the physical schema and
indexes; these models mirror it 1:1 so ORM queries and migrations stay aligned.
The embedding column uses ``pgvector`` and its dimensionality is taken from
``settings.EMBEDDING_DIMENSIONS`` at import time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings

try:  # pragma: no cover - exercised only with pgvector installed
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover
    Vector = None  # type: ignore


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AiJob(Base):
    __tablename__ = "ai_jobs"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), index=True)
    requested_by: Mapped[str | None] = mapped_column(String(128))
    input_type: Mapped[str] = mapped_column(String(32), default="text")
    # Indexed via the explicit "ix_ai_jobs_status" entry in __table_args__ below
    # (not index=True here, to avoid defining the same index twice).
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    current_node: Mapped[str] = mapped_column(String(64), default="queued")
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    options_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    contract_version: Mapped[str] = mapped_column(String(16), default="1.0")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    callback_url: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Idempotency / duplicate-request tracking --------------------------
    # Indexed via the explicit "ix_ai_jobs_fingerprint" entry in __table_args__.
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint_version: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    last_duplicate_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duplicate_request_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    __table_args__ = (
        Index("ix_ai_jobs_tenant_project_created", "tenant_id", "project_id", "created_at"),
        Index("ix_ai_jobs_tenant_project_job", "tenant_id", "project_id", "job_id"),
        Index("ix_ai_jobs_status", "status"),
        Index("ix_ai_jobs_fingerprint", "request_fingerprint"),
    )


class AiJobEvent(Base):
    __tablename__ = "ai_job_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="info")
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiJobAttempt(Base):
    __tablename__ = "ai_job_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="PROCESSING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_ai_job_attempts_job_attempt"),
    )


class AiSourceDocument(Base):
    __tablename__ = "ai_source_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), index=True)
    backend_document_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="text")
    file_name: Mapped[str] = mapped_column(String(512), default="unknown")
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    storage_key: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(Text)
    sha256_hash: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(16), default="en")
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiSourceChunk(Base):
    __tablename__ = "ai_source_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # job_id/project_id are indexed via the explicit "ix_ai_source_chunks_job"/
    # "ix_ai_source_chunks_project" entries in __table_args__ below (not
    # index=True here, to avoid defining the same index twice).
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"))
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[str | None] = mapped_column(String(128))
    source_document_id: Mapped[str | None] = mapped_column(String(32), index=True)
    chunk_id: Mapped[str] = mapped_column(String(128), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    page_number: Mapped[int | None] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(String(128))
    start_time_sec: Mapped[float | None] = mapped_column(Float)
    end_time_sec: Mapped[float | None] = mapped_column(Float)
    start_char: Mapped[int] = mapped_column(Integer, default=0)
    end_char: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_ai_source_chunks_job", "job_id"),
        Index("ix_ai_source_chunks_project", "project_id"),
        UniqueConstraint("job_id", "chunk_id", name="uq_ai_source_chunks_job_chunk"),
    )


class AiSourceChunkEmbedding(Base):
    __tablename__ = "ai_source_chunk_embeddings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    chunk_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), index=True)
    embedding_model: Mapped[str] = mapped_column(String(128))
    # Dimensionality is fixed per deployment via EMBEDDING_DIMENSIONS.
    if Vector is not None:  # pragma: no branch
        embedding: Mapped[list] = mapped_column(Vector(settings.EMBEDDING_DIMENSIONS))
    else:  # pragma: no cover - fallback when pgvector unavailable
        embedding: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiRequirement(Base):
    __tablename__ = "ai_requirements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), index=True)
    requirement_key: Mapped[str] = mapped_column(String(64))
    internal_id: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str | None] = mapped_column(String(256))
    goal: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(32))
    labels_json: Mapped[list] = mapped_column(JSONB, default=list)
    priority: Mapped[str] = mapped_column(String(16), default="Medium")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_type: Mapped[str | None] = mapped_column(String(16))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(Text)
    evidence_match_score: Mapped[float | None] = mapped_column(Float)
    quote_support_score: Mapped[float | None] = mapped_column(Float)
    deduplication_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiRequirementEvidence(Base):
    __tablename__ = "ai_requirement_evidence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    requirement_id: Mapped[str] = mapped_column(String(32), ForeignKey("ai_requirements.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(String(128), index=True)
    chunk_id: Mapped[str | None] = mapped_column(String(128))
    quote: Mapped[str] = mapped_column(Text, default="")
    page_number: Mapped[int | None] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(String(128))
    timestamp: Mapped[str | None] = mapped_column(String(64))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiUserStory(Base):
    __tablename__ = "ai_user_stories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), index=True)
    story_key: Mapped[str] = mapped_column(String(64))
    requirement_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(16), default="Medium")
    type: Mapped[str | None] = mapped_column(String(32))
    labels_json: Mapped[list] = mapped_column(JSONB, default=list)
    deduplication_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    quality_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    jira_fields_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiAcceptanceCriterion(Base):
    __tablename__ = "ai_acceptance_criteria"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_story_id: Mapped[str] = mapped_column(String(32), ForeignKey("ai_user_stories.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(String(128), index=True)
    criterion_key: Mapped[str] = mapped_column(String(64), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    criterion_type: Mapped[str] = mapped_column(String(32), default="plain")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiRequirementCoverage(Base):
    __tablename__ = "ai_requirement_coverages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[str] = mapped_column(String(64))
    coverage_type: Mapped[str] = mapped_column(String(64))
    story_ids_json: Mapped[list] = mapped_column(JSONB, default=list)
    acceptance_criteria_ids_json: Mapped[list] = mapped_column(JSONB, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiQualityReport(Base):
    __tablename__ = "ai_quality_reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True, unique=True)
    overall_score: Mapped[float] = mapped_column(Float, default=1.0)
    traceability_coverage: Mapped[float] = mapped_column(Float, default=1.0)
    groundedness_score: Mapped[float] = mapped_column(Float, default=1.0)
    story_completeness: Mapped[float] = mapped_column(Float, default=1.0)
    acceptance_criteria_quality: Mapped[float] = mapped_column(Float, default=1.0)
    duplicate_risk: Mapped[float] = mapped_column(Float, default=0.0)
    requirement_count: Mapped[int] = mapped_column(Integer, default=0)
    story_count: Mapped[int] = mapped_column(Integer, default=0)
    high_severity_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    report_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiQualityIssue(Base):
    __tablename__ = "ai_quality_issues"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str | None] = mapped_column(String(64))
    item_type: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), default="low")
    rule_violated: Mapped[str] = mapped_column(String(128), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiPipelineWarning(Base):
    __tablename__ = "ai_pipeline_warnings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True)
    node_name: Mapped[str | None] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiJobResult(Base):
    __tablename__ = "ai_job_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("ai_jobs.job_id", ondelete="CASCADE"), index=True, unique=True
    )
    contract_version: Mapped[str] = mapped_column(String(16), default="1.0")
    status: Mapped[str] = mapped_column(String(16), default="completed")
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    exports_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    artifacts_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    processing_time_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


ALL_TABLES = [
    AiJob,
    AiJobEvent,
    AiJobAttempt,
    AiSourceDocument,
    AiSourceChunk,
    AiSourceChunkEmbedding,
    AiRequirement,
    AiRequirementEvidence,
    AiUserStory,
    AiAcceptanceCriterion,
    AiRequirementCoverage,
    AiQualityReport,
    AiQualityIssue,
    AiPipelineWarning,
    AiJobResult,
]
