import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import ResilientLLMClient
from langchain_core.messages import HumanMessage

async def main():
    print("--- Testing Groq LLM ---")
    try:
        groq_client = ResilientLLMClient(primary_provider="groq")
        resp = await groq_client.ainvoke([HumanMessage(content="Say REQURA_GROQ_ONLINE")])
        print("Groq LLM Success:", resp.content.strip()[:100])
        print("Groq Metadata:", resp.response_metadata)
    except Exception as e:
        print("Groq LLM Failed:", type(e).__name__, e)

    print("\n--- Testing OpenAI LLM ---")
    try:
        openai_client = ResilientLLMClient(primary_provider="openai")
        resp = await openai_client.ainvoke([HumanMessage(content="Say REQURA_OPENAI_ONLINE")])
        print("OpenAI LLM Success:", resp.content.strip()[:100])
        print("OpenAI Metadata:", resp.response_metadata)
    except Exception as e:
        print("OpenAI LLM Failed:", type(e).__name__, e)

if __name__ == "__main__":
    asyncio.run(main())
