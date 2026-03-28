from app.schemas.pipeline_state import PipelineState
from app.schemas.items import FunctionalRequirement

async def extract_node(state: PipelineState) -> dict:
    """
    Send raw text to the LLM and extract Functional Requirements.
    """
    print("--- EXTRACT NODE ---")
    raw_text = state.get("raw_text")
    
    if not raw_text:
        return {"error": "EXTRACT_FAILED: no raw text provided"}

    # Mock LLM behavior to output FunctionalRequirements
    try:
        frs = [
            FunctionalRequirement(
                id=1,
                text="The system shall allow users to log in securely.",
                actor="User",
                goal="log in securely",
                source_hint="allow users to log in"
            ),
            FunctionalRequirement(
                id=2,
                text="The application should load within 2 seconds.",
                actor="System",
                goal="load within 2 seconds",
                source_hint="load within 2 seconds"
            )
        ]
        
        if not frs:
            # Format node will set status: "partial"
            return {"functional_requirements": [], "error": "EXTRACT_EMPTY: no functional requirements found"}
            
        return {"functional_requirements": frs}
        
    except Exception as e:
        return {"error": f"EXTRACT_PARSE_ERROR: {str(e)}"}
