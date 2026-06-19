import time
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import JobResult, UserStory, ClassifiedRequirement, StructuredSummary


import time
import re
from typing import Optional, List
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import (
    JobResult,
    SourceDocumentV1,
    SourceRefV1,
    QualityV1,
    JiraFieldsV1,
    RequirementV1,
    AcceptanceCriterionV1,
    UserStoryV1,
    RequirementCoverageV1,
    ExcelExportV1,
    JiraExportV1,
    ExportsV1,
    ExcelFileArtifactV1,
    ArtifactsV1,
    PipelineError,
    StructuredSummary,
    QualityIssue,
    PipelineWarning,
    ExportRow
)


def generate_dedup_key(title_or_text: str) -> str:
    if not title_or_text:
        return ""
    slug = re.sub(r'[^a-z0-9]+', '-', title_or_text.lower()).strip('-')
    return slug[:100]


def parse_pipeline_error(err_str: str, status: str) -> Optional[PipelineError]:
    if not err_str:
        return None
    
    node_name = "unknown"
    code = "PIPELINE_ERROR"
    message = err_str
    
    if "Traceback" in message:
        message = message.split("Traceback")[0].strip()
        
    err_upper = err_str.upper()
    if "EXTRACT" in err_upper:
        node_name = "extract"
        code = "EXTRACT_FAILURE"
    elif "TRANSCRIBE" in err_upper:
        node_name = "transcribe"
        code = "TRANSCRIBE_FAILURE"
    elif "GENERATE" in err_upper:
        node_name = "generate"
        code = "GENERATE_FAILURE"
    elif "CLASSIFY" in err_upper:
        node_name = "classify"
        code = "CLASSIFY_FAILURE"
    elif "QUALITY" in err_upper:
        node_name = "quality_gate"
        code = "QUALITY_GATE_FAILURE"
    elif "PARSE" in err_upper:
        node_name = "parse_to_chunks"
        code = "PARSE_FAILURE"
    elif "INGEST" in err_upper:
        node_name = "ingest"
        code = "INGEST_FAILURE"
        
    if ":" in err_str:
        parts = err_str.split(":", 1)
        potential_code = parts[0].strip()
        if " " not in potential_code and len(potential_code) < 30:
            code = potential_code
            message = parts[1].strip()
            if "Traceback" in message:
                message = message.split("Traceback")[0].strip()
                
    recoverable = (status == "partial")
    
    return PipelineError(
        node_name=node_name,
        code=code,
        message=message,
        recoverable=recoverable
    )


from app.progress import update_progress

async def format_node(state: PipelineState) -> dict:
    """
    Assemble all outputs into final JobResult contract and attach to state.
    """
    print("--- FORMAT NODE ---")
    update_progress(state.get("job_id"), "format", 100, "PROCESSING")

    error = state.get("error")
    stories = state.get("user_stories", []) or []
    reqs = state.get("classified_requirements", []) or []
    q_issues = state.get("quality_issues", []) or []
    warnings = state.get("warnings", []) or []

    # Coerce user stories and requirements into Pydantic models where needed first
    coerced_stories = []
    for s in (stories or []):
        if isinstance(s, UserStory):
            coerced_stories.append(s)
        else:
            data = dict(s) if isinstance(s, dict) else {"title": str(s)}
            data.setdefault("id", "")
            data.setdefault("description", "")
            data.setdefault("acceptance_criteria", [])
            data.setdefault("labels", [])
            data.setdefault("source_requirement_ids", [])
            data.setdefault("evidence_reference", [])
            coerced_stories.append(UserStory(**data))

    coerced_reqs = []
    for r in (reqs or []):
        if isinstance(r, ClassifiedRequirement):
            coerced_reqs.append(r)
        else:
            data = dict(r) if isinstance(r, dict) else {"id": r}
            data.setdefault("text", "")
            data.setdefault("actor", None)
            data.setdefault("goal", None)
            data.setdefault("candidate_labels", [])
            data.setdefault("confidence", 0.0)
            data.setdefault("evidence", [])
            data.setdefault("needs_review", False)
            data.setdefault("review_reason", None)
            if "labels" not in data and "label" in data:
                data["labels"] = [data["label"]]
            data.setdefault("labels", [])
            data.setdefault("classification_confidence", 0.0)
            coerced_reqs.append(ClassifiedRequirement(**data))

    # Map status
    status = "partial"
    if error and not coerced_stories and not coerced_reqs:
        status = "failed"
    elif state.get("is_useful") is False:
        status = "rejected"
    else:
        if state.get("is_useful") and (not coerced_reqs or not coerced_stories):
            status = "partial"
        else:
            has_high = any(
                getattr(q, "severity", None) == "high" 
                or (isinstance(q, dict) and q.get("severity") == "high") 
                for q in q_issues
            )
            if error or has_high or warnings:
                status = "partial"
            else:
                status = "completed"

    # Compute processing time if possible
    started_at = state.get("started_at")
    if started_at:
        processing_time_ms = int(max(0, (time.time() - started_at) * 1000))
    else:
        processing_time_ms = state.get("processing_time_ms", 0)

    # 1. Source documents mapping
    source_docs = []
    file_name = "unknown"
    mime_type = "application/octet-stream"
    file_type = state.get("file_type", "unknown")
    
    source_type = "unknown"
    if file_type:
        ft_lower = file_type.lower()
        if ft_lower in ("text", "txt"):
            source_type = "text"
            mime_type = "text/plain"
        elif ft_lower == "pdf":
            source_type = "pdf"
            mime_type = "application/pdf"
        elif ft_lower in ("docx", "doc"):
            source_type = "docx"
            mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif ft_lower in ("audio", "mp3", "wav", "m4a"):
            source_type = "audio"
            mime_type = "audio/mpeg"
        elif ft_lower == "transcript":
            source_type = "transcript"
            mime_type = "text/plain"
            
    source_metadata = state.get("source_metadata")
    if source_metadata:
        file_name = source_metadata.filename
        mime_type = source_metadata.mime_type
    else:
        meta = state.get("metadata") or {}
        if meta.get("filename"):
            file_name = meta.get("filename")
        elif meta.get("file_name"):
            file_name = meta.get("file_name")
            
    if file_name != "unknown" or file_type != "unknown" or source_metadata:
        source_docs.append(SourceDocumentV1(
            source_id="SRC-001",
            source_type=source_type,
            file_name=file_name,
            mime_type=mime_type,
            language="en"
        ))

    # 2. Requirements mapping
    mapped_reqs = []
    req_id_map = {}  # int -> str ID mapping for stories
    for idx, r in enumerate(coerced_reqs, start=1):
        req_str_id = f"REQ-{str(idx).zfill(3)}"
        req_id_map[r.id] = req_str_id
        
        # Determine requirement type
        req_type = "Unknown"
        labels = getattr(r, "labels", []) or []
        if "FR" in labels:
            req_type = "Functional"
        elif "NFR" in labels or "Constraint" in labels or "Assumption" in labels:
            req_type = "Non-Functional"
        elif "BR" in labels:
            req_type = "Business"
            
        # Determine priority
        priority = "Medium"
        
        # Source refs mapping
        source_refs = []
        for ev in getattr(r, "evidence", []) or []:
            source_refs.append(SourceRefV1(
                source_id="SRC-001",
                source_type="document" if source_type != "audio" else "audio",
                document_name=file_name,
                page=ev.page_number,
                chunk_id=ev.chunk_id,
                quote=ev.quote,
                confidence_score=r.confidence
            ))
            
        # Quality mapping
        req_issues = [
            qi.details if isinstance(qi, QualityIssue) else qi.get("details", "") 
            for qi in q_issues 
            if (getattr(qi, "item_id", None) == r.id or (isinstance(qi, dict) and qi.get("item_id") == r.id)) 
            and (getattr(qi, "item_type", None) == "requirement" or (isinstance(qi, dict) and qi.get("item_type") == "requirement"))
        ]
        score = max(0.0, 1.0 - (len(req_issues) * 0.15))
        quality = QualityV1(
            score=round(score, 2),
            issues=req_issues,
            warnings=[]
        )
        
        # Requirement title generation
        title = r.actor + " wants to " + r.goal if r.actor and r.goal else r.text[:50].strip() + "..."
        
        mapped_reqs.append(RequirementV1(
            id=req_str_id,
            title=title,
            description=r.text,
            type=req_type,
            category="General",
            priority=priority,
            actor=r.actor or "System",
            confidence_score=r.confidence,
            deduplication_key=generate_dedup_key(title),
            source_refs=source_refs,
            quality=quality
        ))

    # 3. User Stories mapping
    mapped_stories = []
    for idx, s in enumerate(coerced_stories, start=1):
        story_str_id = f"US-{str(idx).zfill(3)}"
        
        # Map Linked requirement ID
        linked_req_id = "REQ-001"
        if s.source_requirement_ids:
            linked_req_id = req_id_map.get(s.source_requirement_ids[0], "REQ-001")
            
        # Source refs mapping
        source_refs = []
        for ev in getattr(s, "evidence_reference", []) or []:
            source_refs.append(SourceRefV1(
                source_id="SRC-001",
                source_type="document" if source_type != "audio" else "audio",
                document_name=file_name,
                page=ev.page_number,
                chunk_id=ev.chunk_id,
                quote=ev.quote,
                confidence_score=0.9
            ))
            
        # Quality mapping
        story_issues = [
            qi.details if isinstance(qi, QualityIssue) else qi.get("details", "") 
            for qi in q_issues 
            if (getattr(qi, "item_id", None) == s.id or (isinstance(qi, dict) and qi.get("item_id") == s.id)) 
            and (getattr(qi, "item_type", None) == "story" or (isinstance(qi, dict) and qi.get("item_type") == "story"))
        ]
        score = max(0.0, 1.0 - (len(story_issues) * 0.15))
        quality = QualityV1(
            score=round(score, 2),
            issues=story_issues,
            warnings=[]
        )
        
        # Acceptance criteria mapping
        ac_v1_list = []
        for ac in getattr(s, "acceptance_criteria", []) or []:
            ac_v1_list.append(AcceptanceCriterionV1(
                id=ac.id,
                text=ac.text,
                criterion_type=ac.criterion_type
            ))
            
        jira_fields = JiraFieldsV1(
            issue_type="Story",
            summary=s.title,
            description=s.description,
            acceptance_criteria=[ac.text for ac in ac_v1_list],
            priority="Medium",
            labels=s.labels,
            components=[],
            epic_name="",
            story_points=0
        )
        
        mapped_stories.append(UserStoryV1(
            id=story_str_id,
            requirement_id=linked_req_id,
            title=s.title,
            user_story=s.description,
            acceptance_criteria=ac_v1_list,
            priority="Medium",
            type="Functional",
            deduplication_key=generate_dedup_key(s.title),
            source_refs=source_refs,
            quality=quality,
            jira_fields=jira_fields
        ))

    # 4. Requirement Coverages mapping
    mapped_coverages = []
    internal_coverages = state.get("requirement_coverages", []) or []
    for cov in internal_coverages:
        str_req_id = req_id_map.get(cov.requirement_id, f"REQ-{str(cov.requirement_id).zfill(3)}")
        mapped_coverages.append(RequirementCoverageV1(
            requirement_id=str_req_id,
            coverage_type=cov.coverage_type,
            story_ids=cov.story_ids,
            acceptance_criteria_ids=cov.acceptance_criteria_ids,
            reason=cov.reason
        ))

    # Normalize summary
    summary_val = state.get("summary")
    if isinstance(summary_val, StructuredSummary):
        summary_obj = summary_val
    elif not summary_val:
        summary_obj = StructuredSummary(
            executive_summary="",
            key_decisions=[],
            open_questions=[],
            risks=[],
            assumptions=[],
            action_items=[],
            stakeholders=[],
            scope=[],
            out_of_scope=[]
        )
    else:
        summary_obj = StructuredSummary(
            executive_summary=str(summary_val),
            key_decisions=[],
            open_questions=[],
            risks=[],
            assumptions=[],
            action_items=[],
            stakeholders=[],
            scope=[],
            out_of_scope=[]
        )

    # 5. Exports mapping
    excel_rows = []
    jira_rows = []
    for s in mapped_stories:
        excel_rows.append({
            "id": s.id,
            "title": s.title,
            "user_story": s.user_story,
            "acceptance_criteria": "; ".join([ac.text for ac in s.acceptance_criteria]),
            "type": s.type,
            "priority": s.priority,
            "actor": next((r.actor for r in mapped_reqs if r.id == s.requirement_id), "System"),
            "source_requirement_id": s.requirement_id,
            "source_refs": str([ref.model_dump() for ref in s.source_refs])
        })
        jira_rows.append({
            "issue_type": s.jira_fields.issue_type,
            "summary": s.jira_fields.summary,
            "description": s.jira_fields.description,
            "acceptance_criteria": s.jira_fields.acceptance_criteria,
            "priority": s.jira_fields.priority,
            "labels": s.jira_fields.labels,
            "components": s.jira_fields.components,
            "epic_name": s.jira_fields.epic_name,
            "story_points": s.jira_fields.story_points,
            "source_requirement_id": s.requirement_id
        })
        
    exports = ExportsV1(
        excel=ExcelExportV1(
            available=len(excel_rows) > 0,
            columns=["id", "title", "user_story", "acceptance_criteria", "type", "priority", "actor", "source_requirement_id", "source_refs"],
            rows=excel_rows
        ),
        jira=JiraExportV1(
            available=len(jira_rows) > 0,
            issue_type="Story",
            rows=jira_rows
        )
    )

    artifacts = ArtifactsV1(
        excel_file=ExcelFileArtifactV1(
            available=False,
            file_url="",
            file_name="",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    )

    # Structured error parsing
    structured_error = parse_pipeline_error(error, status)

    # Backward compatible export rows
    legacy_export_rows = state.get("export_rows", []) or []

    job_result = JobResult(
        contract_version="1.0",
        job_id=state.get("job_id", ""),
        status=status,
        is_useful=state.get("is_useful", True),
        relevance_score=state.get("relevance_score", 0.0),
        source_documents=source_docs,
        requirements=mapped_reqs,
        user_stories=mapped_stories,
        requirement_coverages=mapped_coverages,
        summary=summary_obj,
        exports=exports,
        artifacts=artifacts,
        quality_issues=q_issues,
        warnings=warnings,
        error=structured_error,
        processing_time_ms=processing_time_ms,
        
        # Legacy compat
        error_message=error,
        export_rows=legacy_export_rows
    )

    return {"status": status, "is_useful": job_result.is_useful, "error": error, "job_result": job_result}
