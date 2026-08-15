"""
Verification script for real external providers and store backend.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add app to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings, llm_key_for, transcribe_key_for
from app.llm import get_llm
from app.store.factory import get_stores
from app.services.source_processing.models import SourceInput
from app.services.source_processing.audio import process_audio_source
from langchain_core.messages import HumanMessage

async def main():
    print("=== PROVIDER CONNECTIVITY CHECK ===")
    print(f"ENV: {settings.ENV}")
    print(f"LLM Provider: {settings.LLM_PROVIDER}")
    print(f"LLM Model: {settings.OPENROUTER_MODEL if settings.LLM_PROVIDER == 'openrouter' else settings.OPENAI_MODEL}")
    print(f"STT Provider: {settings.TRANSCRIBE_PROVIDER}")
    print(f"STT Model: {settings.GROQ_WHISPER_MODEL}")
    print(f"Use Database: {settings.use_database}")

    # 1. Test Store Bundle
    print("\n--- Testing Store Bundle ---")
    try:
        stores = get_stores()
        print(f"JobStore: {type(stores.jobs).__name__}")
        print(f"ResultStore: {type(stores.results).__name__}")
        print(f"ChunkStore: {type(stores.chunks).__name__}")
        print(f"EmbeddingStore: {type(stores.embeddings).__name__}")
        print("Store Check: SUCCESS")
    except Exception as e:
        print(f"Store Initialization Error: {e}")

    # 2. Test LLM
    print("\n--- Testing Real LLM Call (OpenRouter) ---")
    try:
        llm = get_llm()
        resp = await llm.ainvoke([HumanMessage(content="Respond with 'REQURA_LLM_ONLINE' only.")])
        print(f"LLM Response: {resp.content.strip()}")
        print(f"Response Metadata: {resp.response_metadata}")
        print("LLM Check: SUCCESS")
    except Exception as e:
        print(f"LLM Check FAILED: {type(e).__name__}: {e}")

    # 3. Test STT
    print("\n--- Testing Real STT Call (Groq Whisper via process_audio_source) ---")
    sample_audio_path = Path(__file__).resolve().parent.parent / "test-fixtures" / "verification" / "meeting.mp3"
    if not sample_audio_path.exists():
        print(f"Audio fixture missing at {sample_audio_path}")
    else:
        with open(sample_audio_path, "rb") as f:
            audio_bytes = f.read()
        print(f"Audio fixture loaded: {len(audio_bytes)} bytes")
        try:
            source_input = SourceInput(
                document_id="audio_test_1",
                filename="meeting.mp3",
                file_type="audio",
                raw_bytes=audio_bytes,
                audio_format="mp3",
            )
            processed = await process_audio_source(source_input, job_id="test_conn_job", language="en")
            print(f"Processed Status: {processed.status}")
            print(f"Processed Error: {processed.error_code} - {processed.error_message}")
            print(f"Raw text length: {len(processed.raw_text or '')} chars")
            print(f"Raw text preview: {(processed.raw_text or '')[:200]}...")
            print(f"Chunks count: {len(processed.chunks)}")
            if processed.chunks:
                print(f"First chunk metadata: {processed.chunks[0].chunk_metadata}")
            print("STT Check: SUCCESS")
        except Exception as e:
            print(f"STT Check FAILED: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
