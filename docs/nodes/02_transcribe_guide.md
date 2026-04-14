# Node Guide: Transcribe Node
**Status**: `[UNASSIGNED]`  
**Owner Role**: AI Engineer / Audio Processing Specialist

## 1. Description & Vision
The **Transcribe Node** handles all audio inputs for the pipeline. It is responsible for converting raw audio data into text while maintaining semantic context and speaker identification.

**Vision**: High-accuracy transcription that handles meeting cross-talk, technical jargon, and different audio qualities (MP3, WAV) seamlessly.

## 2. Current Implementation (`transcribe.py`)
- **Logic**: 
    - Uses Gemini to *simulate* a transcription based on `job_id`.
    - Returns a mock business requirements meeting text.
- **Input**: `state.get("job_id")`, `state.get("file_type") == "audio"`.
- **Output**: `{"raw_text": str}` and potential `{"error": str}`.

## 3. Expected Enhancements (TODOs)
- [ ] **Integration**: Replace simulation with a real transcription service (Whisper, AssemblyAI, or Deepgram).
- [ ] **Diarization**: Add speaker markers (e.g., "Speaker 1", "Speaker 2") to the `raw_text` for better LLM context.
- [ ] **Chunking**: Handle long audio files (> 1 hour) by chunking and processing in parallel if needed.
- [ ] **Error Fallback**: If the main model fails, attempt a low-cost backup transcription.

## 4. Operational Guidelines
- **Latency Control**: Transcription is often the slowest node. Any latency > 30s should be handled with a progress indicator for the UI.
- **Cleaning**: Remove filler words ("um," "uh") before passing to the state.
- **Consistency**: Output must produce a single `raw_text` block that follows the same schema as the Ingest node.

## 5. Verification Checklist
- [ ] Does it handle audio files without crashing when the LLM is busy?
- [ ] Does it return `TRANSCRIBE_LLM_FAILURE` when the provider is down?
- [ ] Is the output `raw_text` usable for the `extract_node`?
- [ ] Does it correctly ignore non-audio files if called accidentally?
