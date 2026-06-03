from typing import TypedDict, List, Optional, Dict, Any, Annotated
import operator
from app.schemas.items import (
    DocumentSource,
    SourceChunk,
    ExtractedRequirement,
    ClassifiedRequirement,
    RequirementCoverage,
    UserStory,
    QualityIssue,
    PipelineWarning,
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
    
    # Reducers are mandatory to accumulate lists across parallel chunks/nodes
    chunks: Annotated[List[SourceChunk], operator.add]
    extracted_requirements: Annotated[List[ExtractedRequirement], operator.add]
    classified_requirements: Annotated[List[ClassifiedRequirement], operator.add]
    requirement_coverages: Annotated[List[RequirementCoverage], operator.add]
    user_stories: Annotated[List[UserStory], operator.add]
    quality_issues: Annotated[List[QualityIssue], operator.add]
    warnings: Annotated[List[PipelineWarning], operator.add]
    export_rows: Annotated[List[ExportRow], operator.add]
    
    summary: Optional[StructuredSummary]
    
    # --- Flow Control and Tracking ---
    is_useful: bool
    relevance_score: float
    status: str                     # "success", "partial", "rejected", "error", "needs_review"
    error: Optional[str]
    started_at: float
    processing_time_ms: int

    # --- Legacy Fields (Backwards Compatibility) ---
    functional_requirements: Annotated[List[FunctionalRequirement], operator.add]
