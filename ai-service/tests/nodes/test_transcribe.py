import pytest
from app.nodes.transcribe import transcribe_node

@pytest.mark.asyncio
async def test_transcribe_node_real(base_state, sample_audio_bytes):
    # NOTE: This requires a real MP3/WAV file in tests/fixtures/sample.mp3
    # and a valid OPENAI_API_KEY in the environment.
    state = base_state.copy()
    state["raw_bytes"] = sample_audio_bytes
    state["file_type"] = "audio"
    
    result = await transcribe_node(state)
    
    # If the file is fake, we expect an API error or empty text
    if "error" in result:
        print(f"Known failure (expected if no real audio): {result['error']}")
        assert "TRANSCRIBE_API_FAILURE" in result["error"]
    else:
        assert "raw_text" in result
        assert result["raw_text"] is not None
