import time
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import JobResult, UserStory, ClassifiedRequirement, StructuredSummary


async def format_node(state: PipelineState) -> dict:
    """
    Assemble all outputs into final JobResult contract and attach to state.
    """
    print("--- FORMAT NODE ---")

    error = state.get("error")
    stories = state.get("user_stories", [])
    reqs = state.get("classified_requirements", [])

    # Default to partial; we'll compute precise status below
    status = "partial"

    # Fatal error without outputs => error
    if error and not stories and not reqs:
        status = "error"
    # Explicit rejection
    elif state.get("is_useful") is False:
        status = "rejected"
    else:
        # If useful but both requirements and stories empty => needs_review
        # Instruction says: If is_useful=true and requirements is empty -> needs_review
        # If is_useful=true and user_stories is empty -> needs_review
        if state.get("is_useful") and (not reqs or not stories):
            status = "needs_review"
        else:
            # No fatal error, useful, and both outputs present => success
            status = "success"


    # Compute processing time if possible
    started_at = state.get("started_at")
    if started_at:
        processing_time_ms = int(max(0, (time.time() - started_at) * 1000))
    else:
        processing_time_ms = state.get("processing_time_ms", 0)

    # If any high severity quality issues exist, override status to needs_review
    q_issues = state.get("quality_issues", []) or []
    has_high = any(getattr(q, "severity", None) == "high" or (isinstance(q, dict) and q.get("severity") == "high") for q in q_issues)
    if has_high:
        status = "needs_review"

    # Coerce user stories and requirements into Pydantic models where needed
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
            data.setdefault("labels", [])
            data.setdefault("classification_confidence", 0.0)
            coerced_reqs.append(ClassifiedRequirement(**data))

    # Normalize summary: allow either StructuredSummary or plain text
    summary_val = state.get("summary")
    if isinstance(summary_val, StructuredSummary) or summary_val is None:
        summary_obj = summary_val
    else:
        # Turn plain text into a StructuredSummary executive_summary
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

    job_result = JobResult(
        job_id=state.get("job_id", ""),
        status=status,
        is_useful=state.get("is_useful", True),
        relevance_score=state.get("relevance_score", 0.0),
        user_stories=coerced_stories,
        requirements=coerced_reqs,
        requirement_coverages=state.get("requirement_coverages", []),
        summary=summary_obj,
        export_rows=state.get("export_rows", []),
        quality_issues=q_issues,
        warnings=state.get("warnings", []),
        error_message=error,
        processing_time_ms=processing_time_ms
    )

    # Attach final job_result into returned update (PipelineState now allows `job_result`)
    return {"status": status, "is_useful": job_result.is_useful, "error": error, "job_result": job_result}
