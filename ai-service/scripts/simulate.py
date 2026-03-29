import sys
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Add the app directory to the path so we can import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables from .env
load_dotenv()

from app.graph.pipeline import build_pipeline
from app.schemas.pipeline_state import PipelineState

async def simulate():
    print("=== Pipeline Simulation with Gemini ===")
    
    # Check for Gemini API key
    if not os.getenv("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not found in environment.")
        return

    # Initialize the pipeline
    pipeline = build_pipeline()

    # Define a sample input document (representing a business requirements excerpt)
    sample_text = """
    Document: E-Commerce Platform Requirements
    
    1. The system shall allow users to browse products without logging in.
    2. Registered users MUST be able to add items to a shopping cart.
    3. The checkout process must support payments via Stripe and PayPal.
    4. The application must load and be interactive within 3 seconds for 95% of users.
    5. User passwords must be hashed using argon2 before storage in the database.
    6. All API requests must be logged for auditing purposes.
    """

    # Initial state
    initial_state = {
        "job_id": "sim_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
        "raw_bytes": sample_text.encode('utf-8'),
        "file_type": "pdf",  # Simulate handling as a PDF (text extraction node will mock this)
        "raw_text": sample_text.strip(),
        "functional_requirements": [],
        "classified_requirements": [],
        "user_stories": [],
        "summary": "",
        "status": "started",
        "started_at": datetime.now().timestamp(),
        "error": None
    }

    print("\n[STARTING PIPELINE EXECUTION]")
    try:
        # Run the pipeline
        final_state = await pipeline.ainvoke(initial_state)

        print("\n[RESULTS]")
        
        print("\n--- Summary (Gemini Generated) ---")
        print(final_state.get("summary", "No summary generated."))

        print("\n--- Extracted Requirements ---")
        frs = final_state.get("functional_requirements", [])
        for fr in frs:
            # Pydantic objects use dot notation
            actor = getattr(fr, 'actor', 'N/A')
            text = getattr(fr, 'text', 'N/A')
            goal = getattr(fr, 'goal', 'N/A')
            print(f"- [{actor}] {text} (Goal: {goal})")

        print("\n--- Classified Requirements ---")
        creqs = final_state.get("classified_requirements", [])
        for cr in creqs:
            label = getattr(cr, 'label', 'N/A')
            text = getattr(cr, 'text', 'N/A')
            conf = getattr(cr, 'confidence', 0.0)
            print(f"- {label}: {text} (Confidence: {conf})")

        print("\n--- User Stories ---")
        stories = final_state.get("user_stories", [])
        for story in stories:
            title = getattr(story, 'title', 'N/A')
            desc = getattr(story, 'description', 'N/A')
            print(f"\nStory: {title}")
            print(f"Description: {desc}")
            print("Acceptance Criteria:")
            for ac in getattr(story, 'acceptance_criteria', []):
                ac_type = getattr(ac, 'criterion_type', 'N/A')
                ac_text = getattr(ac, 'text', 'N/A')
                print(f"  - [{ac_type}] {ac_text}")

        print(f"\nFinal Status: {final_state.get('status', 'unknown')}")
        if final_state.get('error'):
            print(f"Errors: {final_state.get('error')}")

    except Exception as e:
        print(f"Simulation failed with error: {e}")

if __name__ == "__main__":
    asyncio.run(simulate())
