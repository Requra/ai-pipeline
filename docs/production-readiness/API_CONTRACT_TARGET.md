# 📜 Target API Contracts & State Schemas

This document defines the strict, production-ready schemas for the Requra.AI pipeline. These schemas are designed to enforce type safety across nodes and provide a stable client contract.

---

## 1. Pipeline State (`PipelineState`)

This schema represents the internal state passed between nodes in the LangGraph.

```python
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
    ExportRow
)

class PipelineState(TypedDict):
    # Inputs
    job_id: str
    raw_bytes: bytes
    file_type: str                  # e.g., "pdf", "docx", "audio", "text"
    metadata: Dict[str, Any]

    # Intermediate State
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
    is_useful: bool
    relevance_score: float
    status: str                     # "success", "partial", "rejected", "error", "needs_review"
    error: Optional[str]
    started_at: float
    processing_time_ms: int
```

---

## 2. Core Pydantic Models

These models will replace the current definitions in `app.schemas.items`.

```python
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

RequirementType = Literal["FR", "NFR", "BR", "Constraint", "Assumption", "Open Question", "Out-of-Scope"]

class DocumentSource(BaseModel):
    filename: str
    file_size_bytes: int
    mime_type: str
    page_count: Optional[int] = None
    sha256_hash: str

class SourceChunk(BaseModel):
    chunk_id: str
    text: str
    start_char: int
    end_char: int
    page_number: Optional[int] = None
    speaker: Optional[str] = None
    start_time_sec: Optional[float] = None
    end_time_sec: Optional[float] = None

class EvidenceSpan(BaseModel):
    chunk_id: str
    quote: str
    page_number: Optional[int] = None
    speaker: Optional[str] = None
    timestamp: Optional[str] = None

class ExtractedRequirement(BaseModel):
    id: int
    text: str
    actor: Optional[str] = None
    goal: Optional[str] = None
    candidate_labels: List[RequirementType]
    confidence: float
    evidence: List[EvidenceSpan] = Field(
        ...,
        min_items=1,
        description="Non-empty list of raw text quotes backing this requirement for grounding."
    )
    needs_review: bool = False
    review_reason: Optional[str] = None

class ClassifiedRequirement(ExtractedRequirement):
    labels: List[RequirementType]
    classification_confidence: float

class RequirementCoverage(BaseModel):
    requirement_id: int
    coverage_type: Literal[
        "covered_by_story",
        "split_into_stories",
        "merged_into_story",
        "attached_as_acceptance_criteria",
        "non_story_requirement",
        "needs_review"
    ]
    story_ids: List[str] = []
    acceptance_criteria_ids: List[str] = []
    reason: Optional[str] = None

class AcceptanceCriterion(BaseModel):
    id: str
    text: str
    criterion_type: Literal["Given-When-Then", "plain"] = "plain"

class UserStory(BaseModel):
    id: str
    title: str
    description: str = Field(description="As a <actor>, I want <goal>, so that <benefit>.")
    acceptance_criteria: List[AcceptanceCriterion]
    source_requirement_ids: List[int] = Field(
        ...,
        description="IDs of source requirements mapping to this story. Supports many-to-one, one-to-many, etc."
    )
    labels: List[RequirementType]
    evidence_reference: List[EvidenceSpan]

class QualityIssue(BaseModel):
    item_id: int
    item_type: Literal["requirement", "story", "coverage"]
    severity: Literal["low", "medium", "high"]
    rule_violated: str
    details: str

class PipelineWarning(BaseModel):
    node_name: str
    code: str
    message: str

class StructuredSummary(BaseModel):
    executive_summary: str
    key_decisions: List[str]
    open_questions: List[str]
    risks: List[str]
    assumptions: List[str]
    action_items: List[str]
    stakeholders: List[str]
    scope: List[str]
    out_of_scope: List[str]

class ExportRow(BaseModel):
    requirement_id: int
    requirement_text: str
    requirement_type: str
    confidence: float
    user_story_ids: str              # Comma-separated list of Story IDs
    user_story_titles: str           # Semicolon-separated titles
    coverage_type: str               # covered_by_story, non_story_requirement, etc.
    acceptance_criteria: str         # Newline separated criteria
    source_quote: str                # Backing quote
    needs_review: bool
    review_reason: str
```

---

## 3. API Response Contract (`JobResult`)

This is the JSON contract returned to the client, providing a clean separation from graph internals.

```python
class JobResult(BaseModel):
    job_id: str
    status: Literal["success", "partial", "rejected", "error", "needs_review"]
    is_useful: bool
    relevance_score: float
    user_stories: List[UserStory]
    requirements: List[ClassifiedRequirement]
    requirement_coverages: List[RequirementCoverage]
    summary: Optional[StructuredSummary] = None
    export_rows: List[ExportRow]
    quality_issues: List[QualityIssue]
    warnings: List[PipelineWarning]
    error_message: Optional[str] = None
    processing_time_ms: int
```
