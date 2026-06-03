from app.schemas.pipeline_state import PipelineState

def route_after_ingest(state: PipelineState) -> str:
    """
    If there is an error OR the document is rejected by AI, go straight to format. 
    Otherwise route to transcribe if audio, else parse_to_chunks.
    """
    if state.get("error") or state.get("is_useful") is False:
        return "format"   # short-circuit: skip processing if useless or error

    return "transcribe" if state.get("file_type") == "audio" else "parse_to_chunks"
