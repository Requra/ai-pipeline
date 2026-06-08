import time
from app.schemas.pipeline_state import PipelineState
from app.schemas.items import JobResult


async def format_node(state: PipelineState) -> dict:
    """
    Assemble all outputs into final JobResult contract and attach to state.
    """
    print("--- FORMAT NODE ---")

    error = state.get("error")
    stories = state.get("user_stories", [])
    reqs = state.get("classified_requirements", [])

    if error and not stories and not reqs:
        status = "error"
    elif state.get("is_useful") is False:
        status = "rejected"
    elif error and (stories or reqs):
        status = "partial"
    else:
        status = "success"

    job_result = JobResult(
        job_id=state.get("job_id", ""),
        status=status,
        is_useful=state.get("is_useful", True),
        relevance_score=state.get("relevance_score", 0.0),
        user_stories=stories or [],
        requirements=reqs or [],
        requirement_coverages=state.get("requirement_coverages", []),
        summary=state.get("summary"),
        export_rows=state.get("export_rows", []),
        quality_issues=state.get("quality_issues", []),
        warnings=state.get("warnings", []),
        error_message=error,
        processing_time_ms=state.get("processing_time_ms", 0)
    )

    # Attach final job result to state so the pipeline returns a stable contract
    return {"job_result": job_result, "status": status}
