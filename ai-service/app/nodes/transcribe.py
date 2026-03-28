from app.schemas.pipeline_state import PipelineState

async def transcribe_node(state: PipelineState) -> dict:
    """
    Convert audio files to text via Whisper API. Only runs when file_type == 'audio'.
    """
    print("--- TRANSCRIBE NODE ---")
    
    # Mock transcription
    # In reality, chunk audio if > 25MB and handle Whisper API timeouts/silent audio
    mock_transcription = "Mock transcribed audio text outlining functional requirements."
    
    if not mock_transcription:
        return {"error": "TRANSCRIBE_EMPTY: audio transcription yielded no text"}
        
    return {"raw_text": mock_transcription}
