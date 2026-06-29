from pydantic import BaseModel, Field
from typing import Optional, List, Literal

# --- Production Constants & Types ---

RequirementType = Literal["FR", "NFR", "BR", "Constraint", "Assumption", "Open Question", "Out-of-Scope"]

# --- Core Production Models ---

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

class RetrievedChunk(BaseModel):
    """A chunk returned by the lexical retriever, annotated with its score.

    Used internally for evidence retrieval/grounding; never part of the public
    JobResult contract.
    """
    chunk_id: str
    text: str
    score: float
    page_number: Optional[int] = None
    start_char: int = 0
    end_char: int = 0
    speaker: Optional[str] = None
    timestamp: Optional[str] = None


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
    candidate_labels: List[RequirementType] = Field(default_factory=list)
    confidence: float
    evidence: List[EvidenceSpan] = Field(
        default_factory=list,
        description="Non-empty list of raw text quotes backing this requirement for grounding."
    )
    priority: str = "Medium"
    # Whether the requirement is stated directly in the source ("explicit") or
    # inferred from context ("implied"). Optional; informational for grounding.
    extraction_type: Optional[Literal["explicit", "implied"]] = None
    # Retrieval-grounding scores (Phase 5). Internal only; not in the V1 contract.
    # evidence_match_score: best lexical retrieval score of a supporting chunk.
    # quote_support_score: fraction of this requirement's quotes found in source.
    evidence_match_score: Optional[float] = None
    quote_support_score: Optional[float] = None
    needs_review: bool = False
    review_reason: Optional[str] = None

class ClassifiedRequirement(ExtractedRequirement):
    labels: List[RequirementType]
    classification_confidence: float = 0.0

class RequirementCoverage(BaseModel):
    requirement_id: int
    coverage_type: Literal[
        "covered_by_story",
        "split_into_stories",
        "merged_into_story",
        "attached_as_acceptance_criteria",
        "non_story",
        "needs_review"
    ]

    story_ids: List[str] = []
    acceptance_criteria_ids: List[str] = []
    reason: Optional[str] = None

class AcceptanceCriterion(BaseModel):
    id: str = ""
    text: str
    criterion_type: Literal["Given-When-Then", "plain"] = "plain"

class UserStory(BaseModel):
    id: str = ""
    title: str
    description: str = Field(description="As a <actor>, I want <goal>, so that <benefit>.")
    acceptance_criteria: List[AcceptanceCriterion]
    source_requirement_ids: List[int] = Field(
        default_factory=list,
        description="IDs of source requirements mapping to this story. Supports many-to-one, one-to-many, etc."
    )
    labels: List[RequirementType]
    priority: str = "Medium"
    evidence_reference: List[EvidenceSpan] = Field(default_factory=list)

    # Legacy Fields (Backwards Compatibility)
    source_fr_id: Optional[int] = None

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
    coverage_type: str               # covered_by_story, non_story, etc.
    acceptance_criteria: str         # Newline separated criteria
    source_quote: str                # Backing quote
    needs_review: bool
    review_reason: str

class JobResult(BaseModel):
    contract_version: str = "1.0"
    job_id: str
    status: Literal["completed", "partial", "failed", "rejected"]
    is_useful: bool
    relevance_score: float
    source_documents: List["SourceDocumentV1"] = Field(default_factory=list)
    requirements: List["RequirementV1"] = Field(default_factory=list)
    user_stories: List["UserStoryV1"] = Field(default_factory=list)
    requirement_coverages: List["RequirementCoverageV1"] = Field(default_factory=list)
    summary: Optional[StructuredSummary] = None
    exports: "ExportsV1" = Field(default_factory=lambda: ExportsV1())
    artifacts: "ArtifactsV1" = Field(default_factory=lambda: ArtifactsV1())
    quality_issues: List[QualityIssue] = Field(default_factory=list)
    warnings: List[PipelineWarning] = Field(default_factory=list)
    error: Optional["PipelineError"] = None
    processing_time_ms: int

    # Deprecated fields for backward compatibility
    error_message: Optional[str] = Field(default=None, deprecated=True)
    export_rows: List[ExportRow] = Field(default_factory=list, deprecated=True)


# --- Contract V1 Models ---

class SourceDocumentV1(BaseModel):
    source_id: str
    source_type: Literal["text", "pdf", "docx", "audio", "transcript", "unknown"]
    file_name: str
    mime_type: str
    language: str = "en"


class SourceRefV1(BaseModel):
    source_id: str
    source_type: str = "document"
    document_name: str
    page: Optional[int] = None
    chunk_id: str
    quote: str
    confidence_score: float


class QualityV1(BaseModel):
    score: float = 1.0
    issues: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class JiraFieldsV1(BaseModel):
    issue_type: str = "Story"
    summary: str
    description: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    priority: str = "Medium"
    labels: List[str] = Field(default_factory=list)
    components: List[str] = Field(default_factory=list)
    epic_name: str = ""
    story_points: int = 0


class RequirementV1(BaseModel):
    id: str
    title: str
    description: str
    type: Literal["Functional", "Non-Functional", "Business", "Unknown"]
    category: str = "General"
    priority: Literal["Low", "Medium", "High", "Critical", "Unknown"] = "Medium"
    actor: str = "System"
    confidence_score: float = 0.0
    deduplication_key: str = ""
    source_refs: List[SourceRefV1] = Field(default_factory=list)
    quality: QualityV1 = Field(default_factory=QualityV1)


class AcceptanceCriterionV1(BaseModel):
    id: str = ""
    text: str
    criterion_type: Literal["Given-When-Then", "plain"] = "plain"


class UserStoryV1(BaseModel):
    id: str
    requirement_id: str
    title: str
    user_story: str
    acceptance_criteria: List[AcceptanceCriterionV1] = Field(default_factory=list)
    priority: Literal["Low", "Medium", "High", "Critical", "Unknown"] = "Medium"
    type: Literal["Functional", "Non-Functional", "Business", "Unknown"] = "Functional"
    deduplication_key: str = ""
    source_refs: List[SourceRefV1] = Field(default_factory=list)
    quality: QualityV1 = Field(default_factory=QualityV1)
    jira_fields: JiraFieldsV1


class RequirementCoverageV1(BaseModel):
    requirement_id: str
    coverage_type: str
    story_ids: List[str] = Field(default_factory=list)
    acceptance_criteria_ids: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class ExcelExportV1(BaseModel):
    available: bool = False
    columns: List[str] = Field(default_factory=list)
    rows: List[dict] = Field(default_factory=list)


class JiraExportV1(BaseModel):
    available: bool = False
    issue_type: str = "Story"
    rows: List[dict] = Field(default_factory=list)


class ExportsV1(BaseModel):
    excel: ExcelExportV1 = Field(default_factory=ExcelExportV1)
    jira: JiraExportV1 = Field(default_factory=JiraExportV1)


class ExcelFileArtifactV1(BaseModel):
    available: bool = False
    file_url: str = ""
    file_name: str = ""
    mime_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ArtifactsV1(BaseModel):
    excel_file: ExcelFileArtifactV1 = Field(default_factory=ExcelFileArtifactV1)


class PipelineError(BaseModel):
    node_name: str
    code: str
    message: str
    recoverable: bool


# --- Legacy Models (Backwards Compatibility) ---

class FunctionalRequirement(BaseModel):
    id: int
    text: str
    actor: Optional[str] = None
    goal: Optional[str] = None
    source_hint: str = ""
