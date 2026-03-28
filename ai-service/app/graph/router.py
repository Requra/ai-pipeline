from app.schemas.pipeline_state import PipelineState

def route_after_ingest(state: PipelineState) -> str:
    """
    If there is an error, go straight to format. 
    Otherwise route to transcribe if audio, else extract.
    """
    if state.get("error"):
        return "format"   # short-circuit: always return structured response

    return "transcribe" if state.get("file_type") == "audio" else "extract"
