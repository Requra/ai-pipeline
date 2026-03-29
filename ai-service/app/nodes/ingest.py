from app.schemas.pipeline_state import PipelineState

def extract_pdf(raw_bytes: bytes) -> str:
    # Mock PDF extraction
    return "Mocked PDF content with functional requirements."

def extract_docx(raw_bytes: bytes) -> str:
    # Mock DOCX extraction
    return "Mocked DOCX content with functional requirements."

async def ingest_node(state: PipelineState) -> dict:
    """
    Receive the raw uploaded file. Validate it, detect its type, and extract plain text.
    The only node that touches raw bytes — all downstream nodes work with clean strings.
    """
    print("--- INGEST NODE ---")
    if state.get("file_type") == "audio":
        return {"raw_text": None}  # Transcribe node handles audio

    try:
        raw_text = state.get("raw_text")
        
        if not raw_text:
            if state.get("file_type") == "pdf":
                raw_text = extract_pdf(state.get("raw_bytes", b""))
            elif state.get("file_type") == "docx":
                raw_text = extract_docx(state.get("raw_bytes", b""))

        if not raw_text or len(raw_text.strip()) < 50:
            return {"error": f"INGEST_EMPTY: extracted text is too short ({len(raw_text.strip()) if raw_text else 0} chars) to process"}

        return {"raw_text": raw_text.strip()}
    except Exception as e:
        return {"error": f"INGEST_FAILED: {str(e)}"}
