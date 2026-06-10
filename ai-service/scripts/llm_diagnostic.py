import os
import asyncio
import json
import sys

# Add ai-service to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.llm import get_llm
from app.nodes.extract import ExtractionResponse

async def run_diagnostic():
    print("--- LLM DIAGNOSTIC ---")
    print(f"Provider: {settings.LLM_PROVIDER}")
    
    if settings.LLM_PROVIDER == "openrouter":
        print(f"Model: {settings.OPENROUTER_MODEL}")
        print(f"Base URL: {settings.OPENROUTER_BASE_URL}")
    elif settings.LLM_PROVIDER == "openai":
        print(f"Model: {settings.OPENAI_MODEL}")
    
    try:
        llm = get_llm()
        print("\n1. Testing Plain Invoke...")
        resp = await llm.ainvoke("Say 'Diagnostic Pass' if you can read this.")
        content = getattr(resp, "content", None) or str(resp)
        print(f"Result: {content.strip()}")
        
        print("\n2. Testing JSON Extraction...")
        system_text = (
            "Extract requirements. Return valid JSON only. No markdown.\n"
            "Shape: {\"requirements\": [{\"id\": 1, \"text\": \"...\", \"candidate_labels\": [\"FR\"], \"confidence\": 1.0}]}"
        )
        user_text = "The system shall allow users to login."
        
        raw = await llm.ainvoke([
            ("system", system_text),
            ("user", user_text)
        ])
        content = getattr(raw, "content", None) or str(raw)
        print(f"Raw Output: {content[:200]}...")
        
        # Simple cleanup
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].startswith("```"): lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        parsed = json.loads(content)
        validated = ExtractionResponse.model_validate(parsed)
        print(f"JSON Parse/Validation: SUCCESS ({len(validated.requirements)} items)")
        
        print("\nDIAGNOSTIC COMPLETED SUCCESSFULLY")
        
    except Exception as e:
        print(f"\nDIAGNOSTIC FAILED")
        print(f"Error type: {type(e).__name__}")
        print(f"Error details: {repr(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
