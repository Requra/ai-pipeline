from app.schemas.pipeline_state import PipelineState
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

class RelevanceCheck(BaseModel):
    is_useful: bool = Field(description="True if the document contains project requirements, meeting notes, or technical specs.")
    relevance_score: float = Field(description="Confidence score between 0 and 1.")
    reason: str = Field(description="Short reason why the document is accepted or rejected.")

def extract_pdf(raw_bytes: bytes) -> str:
    # Mock PDF extraction
    return "The system shall process payments. The system must support user login. Performance must be under 2s."

def extract_docx(raw_bytes: bytes) -> str:
    # Mock DOCX extraction
    return "Meeting Notes: We discussed adding a new search feature to the website for better discovery."

async def ingest_node(state: PipelineState) -> dict:
    """
    Receive the raw uploaded file. Validate it, detect its type, and extract plain text.
    Also performs an AI Quick Scan to reject irrelevant documents.
    """
    print("--- INGEST NODE ---")
    
    # 1. Extraction Phase
    if state.get("file_type") == "audio" and not state.get("raw_text"):
        return {"raw_text": None, "is_useful": True, "relevance_score": 1.0}

    try:
        raw_text = state.get("raw_text")
        if not raw_text:
            if state.get("file_type") == "pdf":
                raw_text = extract_pdf(state.get("raw_bytes", b""))
            elif state.get("file_type") == "docx":
                raw_text = extract_docx(state.get("raw_bytes", b""))

        if not raw_text or len(raw_text.strip()) < 50:
            return {
                "is_useful": False, 
                "relevance_score": 0.0,
                "error": f"INGEST_EMPTY: text too short ({len(raw_text.strip()) if raw_text else 0} chars)"
            }

        cleaned_text = raw_text.strip()

        # 2. Relevance Check Phase (AI Smart Filter)
        llm = get_llm()
        structured_llm = llm.with_structured_output(RelevanceCheck)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a document gatekeeper. Your job is to determine if a document contains project requirements, specifications, or meeting notes relevant to software development. If it's trash (spam, random lists, unrelated info), set is_useful to False."),
            ("user", "Document snippet: {snippet}")
        ])
        
        chain = prompt | structured_llm
        # Check first 2000 chars for efficiency
        check = await chain.ainvoke({"snippet": cleaned_text[:2000]})

        if not check.is_useful:
            return {
                "raw_text": cleaned_text,
                "is_useful": False,
                "relevance_score": check.relevance_score,
                "status": "rejected",
                "error": f"DOCUMENT_REJECTED: {check.reason}"
            }

        return {
            "raw_text": cleaned_text,
            "is_useful": True,
            "relevance_score": check.relevance_score
        }

    except Exception as e:
        return {"error": f"INGEST_FAILED: {str(e)}"}
