import os
import io
from openai import OpenAI
from app.schemas.pipeline_state import PipelineState

async def transcribe_node(state: PipelineState) -> dict:
    """
    Transcribe audio bytes using OpenAI Whisper.
    """
    print("--- TRANSCRIBE NODE ---")
    
    raw_bytes = state.get("raw_bytes")
    if not raw_bytes:
        return {"raw_text": None, "error": "TRANSCRIBE_NO_BYTES: No audio data provided."}

    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # We need to provide a filename and content-type for the API
        # Using a generic name as we don't know the exact format (MP3/WAV)
        audio_file = io.BytesIO(raw_bytes)
        audio_file.name = "audio_input.mp3"  

        response = client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file,
            language="en"
        )
        
        return {"raw_text": response.text}
        
    except Exception as e:
        print(f"Transcribe node Whisper failure: {e}")
        return {"raw_text": None, "error": f"TRANSCRIBE_API_FAILURE: {str(e)}"}

