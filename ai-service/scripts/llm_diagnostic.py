"""
Run this script locally to diagnose your OpenAI / LangChain client setup.
Usage: python ai-service/scripts/llm_diagnostic.py
Make sure OPENAI_API_KEY and OPENAI_MODEL are set in your environment.
"""
import os
import asyncio
import json
import traceback

from app.llm import get_llm
from app.nodes.extract import ExtractionResponse

TEST_TEXT = (
    "The user wants to login (FR). The system must encrypt passwords (NFR). "
    "There is an assumption that SSO is handled by the provider."
)

async def run():
    llm = get_llm()
    print("LLM instance:", llm)
    try:
        print("Trying with_structured_output...")
        structured = llm.with_structured_output(ExtractionResponse)
        # Some LLM clients may not support a synchronous demo; try a minimal call
        res = await structured.ainvoke(TEST_TEXT)
        print("Structured response:", res)
    except Exception as e:
        print("Structured output failed:", type(e).__name__, repr(e))
        traceback.print_exc()
        try:
            print("Trying plain JSON fallback...")
            # Ask the model to return strict JSON matching the extraction schema
            prompt = (
                "Return a JSON object with key 'requirements' as a list of requirement objects. "
                "Each requirement must have id, text, candidate_labels (list), confidence (0-1), evidence (list of {chunk_id, quote}), needs_review (bool). "
                "Return ONLY valid JSON.\n\nText:\n" + TEST_TEXT
            )
            raw = await llm.ainvoke(prompt)
            content = getattr(raw, "content", None) or str(raw)
            print("Raw content:\n", content)
            parsed = json.loads(content)
            print("Parsed JSON:\n", json.dumps(parsed, indent=2))
        except Exception as e2:
            print("JSON fallback also failed:", type(e2).__name__, repr(e2))
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run())
