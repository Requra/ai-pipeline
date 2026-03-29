from app.schemas.pipeline_state import PipelineState
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate

async def transcribe_node(state: PipelineState) -> dict:
    """
    Simulate audio transcription refined by Gemini.
    """
    print("--- TRANSCRIBE NODE ---")
    
    # In a real scenario, this would use Whisper or Gemini 1.5 Flash directly
    # For simulation, we'll use Gemini to "predict" what was said based on job context
    try:
        llm = get_llm()
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are simulating a transcription service. Generate a realistic transcription of a business requirements meeting based on the job ID and context."),
            ("user", "ID: {job_id}")
        ])
        
        chain = prompt | llm
        response = await chain.ainvoke({"job_id": state.get("job_id", "unknown")})
        
        return {"raw_text": response.content}
        
    except Exception as e:
        print(f"Transcribe node Gemini failure: {e}")
        return {"raw_text": "Mock transcribed audio text outlining functional requirements.", "error": f"TRANSCRIBE_LLM_FAILURE: {str(e)}"}
