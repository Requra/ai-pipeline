from typing import TypedDict, List, Optional
from app.schemas.items import FunctionalRequirement, ClassifiedRequirement, UserStory

class PipelineState(TypedDict):
    # Inputs
    job_id: str
    raw_bytes: bytes
    file_type: str  # e.g., "pdf", "docx", "audio"

    # Intermediate State
    raw_text: Optional[str]
    functional_requirements: List[FunctionalRequirement]
    classified_requirements: List[ClassifiedRequirement]
    user_stories: List[UserStory]
    summary: str
    
    # Flow Control and Tracking
    is_useful: bool
    relevance_score: float
    error: Optional[str]
    started_at: float
    status: str
