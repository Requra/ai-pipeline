from typing import TypedDict, List, Optional, Dict, Any
from app.schemas.items import (
    DocumentSource,
    SourceChunk,
    ExtractedRequirement,
    ClassifiedRequirement,
    RequirementCoverage,
    UserStory,
    QualityIssue,
    PipelineWarning,
    JobResult,
    StructuredSummary,
    ExportRow,
    FunctionalRequirement
)

class PipelineState(TypedDict):
    # --- Inputs ---
    job_id: str
    raw_bytes: bytes
    file_type: str                  # e.g., "pdf", "docx", "audio", "text"
    metadata: Dict[str, Any]

    # --- Intermediate State ---
    raw_text: Optional[str]
    source_metadata: Optional[DocumentSource]
    
    # NOTE: For MVP we use simple replacement semantics for list fields.
    # Previously these fields used Annotated([...], operator.add) reducers to
    # support append-style accumulation in true parallel LangGraph fan-out
    # scenarios. That behavior can cause duplication in sequential pipelines
    # when nodes return full updated lists. When/if the graph is converted to
    # use real parallel send/fan-out edges, reintroduce reducer annotations
    # (e.g., Annotated[List[T], operator.add]) as appropriate.
    chunks: List[SourceChunk]

    # RAG source index (Phase 2). The retriever itself lives in a per-job
    # registry (app.rag.source_index) to keep this state JSON-serializable;
    # only the handle + lightweight stats are stored here.
    source_index_id: Optional[str]
    retrieval_stats: Optional[Dict[str, Any]]

    extracted_requirements: List[ExtractedRequirement]
    classified_requirements: List[ClassifiedRequirement]
    requirement_coverages: List[RequirementCoverage]
    user_stories: List[UserStory]
    quality_issues: List[QualityIssue]
    warnings: List[PipelineWarning]
    export_rows: List[ExportRow]
    
    summary: Optional[StructuredSummary]
    # Aggregate quality scores produced by `quality_gate` (Phase 7).
    quality_report: Optional[Dict[str, Any]]
    # Final serialized job result produced by `format` node
    job_result: Optional[JobResult]
    
    # --- Flow Control and Tracking ---
    is_useful: bool
    relevance_score: float
    status: str                     # "success", "partial", "rejected", "error", "needs_review"
    error: Optional[str]
    started_at: float
    processing_time_ms: int

    # --- Legacy Fields (Backwards Compatibility) ---
    # Use replacement semantics for legacy lists as well.
    functional_requirements: List[FunctionalRequirement]
