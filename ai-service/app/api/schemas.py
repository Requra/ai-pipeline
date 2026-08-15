"""Request/response schemas for the internal job API."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

InputTypeLiteral = Literal[
    "text",
    "backend_document",
    "backend_transcript",
    "backend_audio",
    "backend_sources",
]


class SourceDocumentIn(BaseModel):
    document_id: str
    filename: Optional[str] = None
    file_type: Optional[str] = None
    mime_type: Optional[str] = None
    storage_key: Optional[str] = None
    file_url: Optional[str] = None
    # Accept either "hash" or "sha256_hash" from the backend.
    hash: Optional[str] = Field(default=None, alias="sha256_hash")
    page_count: Optional[int] = None

    model_config = {"populate_by_name": True}


class JobOptionsIn(BaseModel):
    generate_user_stories: bool = True
    generate_summary: bool = True
    enable_embeddings: bool = False
    enable_hybrid_retrieval: bool = False
    language: str = "en"
    callback_url: Optional[str] = None
    priority: str = "normal"


class CreateJobRequest(BaseModel):
    job_id: str
    tenant_id: Optional[str] = None
    project_id: str
    requested_by: Optional[str] = None
    user_id: Optional[str] = None
    input_type: InputTypeLiteral
    source_documents: List[SourceDocumentIn] = Field(default_factory=list)
    content: Optional[str] = None
    options: JobOptionsIn = Field(default_factory=JobOptionsIn)
    # When true, an existing job is reprocessed as a new attempt instead of
    # returning the existing record (idempotency override).
    reprocess: bool = False


class CreateJobResponse(BaseModel):
    """Response envelope for ``POST /internal/jobs``.

    Not every field is present on every outcome (freshly-created vs. idempotent
    duplicate vs. retried) — the endpoint returns only the applicable subset.
    Conflicts (``409``) use a distinct ``{"error": {...}}`` shape instead of
    this model (see ``docs/production-architecture.md``).
    """

    job_id: str
    status: str
    attempt_number: Optional[int] = None
    idempotent: bool = False
    links: dict = Field(default_factory=dict)
    # Present when idempotent=True against an active (QUEUED/PROCESSING) job.
    progress_pct: Optional[int] = None
    current_node: Optional[str] = None
    duplicate_of: Optional[str] = None
    message: Optional[str] = None
    # Present when idempotent=True against a completed/partial/rejected job.
    result_available: Optional[bool] = None
    # Present when an explicit retry (reprocess=true) was accepted.
    retried: Optional[bool] = None


class RegenerateStoryRequest(BaseModel):
    """Request to regenerate a single user story with human feedback."""
    requirement_text: str
    requirement_type: str = "FR"
    actor: Optional[str] = None
    goal: Optional[str] = None
    priority: str = "Medium"
    feedback: str
    original_story: Optional[str] = None
    source_context: Optional[str] = None


class AcceptanceCriterionOut(BaseModel):
    id: str
    text: str
    criterion_type: str = "Given-When-Then"


class RegenerateStoryResponse(BaseModel):
    """A single regenerated user story."""
    title: str
    description: str
    acceptance_criteria: List[AcceptanceCriterionOut] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)


class ProcessJsonRequest(BaseModel):
    """Request schema for JSON text/transcript compatibility uploads."""
    job_id: str
    project_id: str
    tenant_id: Optional[str] = None
    requested_by: Optional[str] = None
    source_type: Literal["text", "meeting_transcript"] = "text"
    content: str
    source_documents: List[SourceDocumentIn] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    options: JobOptionsIn = Field(default_factory=JobOptionsIn)
    reprocess: bool = False

