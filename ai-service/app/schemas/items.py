from pydantic import BaseModel
from typing import Optional, List, Literal

class FunctionalRequirement(BaseModel):
    id: int
    text: str
    actor: Optional[str] = None
    goal: Optional[str] = None
    source_hint: str

class ClassifiedRequirement(FunctionalRequirement):
    # label: Literal["FR", "NFR", "BR"]
    labels: List[Literal["FR", "NFR", "BR"]]
    confidence: float

class AcceptanceCriterion(BaseModel):
    text: str
    criterion_type: Literal["Given-When-Then", "plain"]

class UserStory(BaseModel):
    title: str
    description: str
    acceptance_criteria: List[AcceptanceCriterion]
    source_fr_id: int
    # label: Literal["FR", "NFR", "BR"]
    labels: List[Literal["FR", "NFR", "BR"]]

class JobResult(BaseModel):
    job_id: str
    status: Literal["success", "partial", "error"]
    user_stories: List[UserStory]
    requirements: List[ClassifiedRequirement]
    summary: str
    error_message: Optional[str] = None
    processing_time_ms: int
