from app.schemas.pipeline_state import PipelineState
from app.schemas.items import FunctionalRequirement
from app.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List
import re                                             # Regex for preprocessing
import asyncio                                        # Async + parallel execution


class ExtractionResponse(BaseModel):
    requirements: List[FunctionalRequirement] = Field(description="A list of extracted functional requirements.")

def preprocess_text(text: str) -> str:
    """
    Clean raw text before sending to LLM.
    - Remove extra spaces
    - Remove filler words from speech (Whisper artifacts)
    """
    text = text.strip()  # Remove leading/trailing spaces
    text = re.sub(r"\s+", " ", text)  # Normalize multiple spaces into one

    # Remove common filler words 
    text = re.sub(
        r"(uh|um|er|ah|)",
        "",
        text,
        flags=re.IGNORECASE
    )

    return text

def chunk_text_by_words(text: str, threshold: int = 1000, num_chunks: int = 5) -> List[str]:
    """
    Split text into chunks based on word count.

    - If text is small → return 1 chunk
    - If large → split into exactly 5 chunks
    """
    words = text.split()  # Split text into words

    # If text is small, return single chunk
    if len(words) <= threshold:
        return [text]

    # Otherwise, split into equal chunks
    chunk_size = len(words) // num_chunks

    chunks = []
    for i in range(num_chunks):
        start = i * chunk_size
        end = (i + 1) * chunk_size

        # Last chunk takes remaining words
        if i == num_chunks - 1:
            end = len(words)

        chunk_words = words[start:end]
        chunks.append(" ".join(chunk_words))

    return chunks 

def deduplicate(reqs: List[FunctionalRequirement]) -> List[FunctionalRequirement]:
    """
    Remove duplicate requirements based on (actor + goal).
    """
    seen = set()
    result = []

    for r in reqs:
        key = (r.actor.lower(), r.goal.lower())

        if key not in seen:
            seen.add(key)
            result.append(r)

    return result

def normalize_ids(reqs: List[FunctionalRequirement]) -> List[FunctionalRequirement]:
    """
    Ensure IDs are sequential (1, 2, 3, ...)
    """
    for i, r in enumerate(reqs, start=1):
        r.id = i
    return reqs

async def process_chunk(chain, chunk: str) -> List[FunctionalRequirement]:
    """
    Process one chunk using LLM.
    This function is designed to run in parallel.
    """
    response = await chain.ainvoke({"text": chunk})  # Send chunk to LLM

    if not response or not response.requirements:
        return []

    reqs = response.requirements

    # Add traceability (source_hint)
    for r in reqs:
        if not getattr(r, "source_hint", None):
            r.source_hint = chunk[:100]  # Store snippet from source text

    return reqs


async def extract_node(state: PipelineState) -> dict:
    """
    Send raw text to Gemini and extract Functional Requirements using structured output.
    """
    print("--- EXTRACT NODE ---")
    raw_text = state.get("raw_text")
    
    if not raw_text:
        return {"error": "EXTRACT_FAILED: no raw text provided"}

    try:
         # ==============================
        # 1. Preprocessing
        # ==============================
        clean_text = preprocess_text(raw_text)

        # ==============================
        # 2. Chunking
        # ==============================
        chunks = chunk_text_by_words(clean_text)

        # Get Gemini LLM
        llm = get_llm()
        
        # Define structured output
        structured_llm = llm.with_structured_output(ExtractionResponse)
        
        # prompt = ChatPromptTemplate.from_messages([
        #     ("system", "You are an expert requirements engineer. Extract a list of functional requirements from the provided document text. Each requirement should have an ID (starting from 1), text, actor, and goal."),
        #     ("user", "{text}")
        # ])

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """
                You are a senior requirements engineer.

                Extract ONLY functional requirements.

                Rules:
                - Each requirement must include: ID (starting from 1), text, actor, goal
                - Ignore non-functional requirements (performance, security, usability)
                - Each requirement must be atomic (one action only)
                - Split multiple actions into separate requirements
                - Infer actor if missing (User or System)
                - Do NOT invent requirements
                - Avoid duplicates

                Return structured output only.
                """
            ),
            ("user", "{text}")
        ])

        
        chain = prompt | structured_llm

        tasks = [
            process_chunk(chain, chunk)  # Create async task per chunk
            for chunk in chunks
        ]

        results = await asyncio.gather(*tasks)  # Run all tasks concurrently

        # ==============================
        # 5. Merge Results
        # ==============================
        all_requirements = [
            r
            for sublist in results
            for r in sublist
        ]

        if not all_requirements:
            return {
                "functional_requirements": [],
                "error": "EXTRACT_EMPTY: no functional requirements found"
            }

        # ==============================
        # 6. Post-processing
        # ==============================
        all_requirements = deduplicate(all_requirements)
        all_requirements = normalize_ids(all_requirements)

        return {
            "functional_requirements": all_requirements
        }

        
    except Exception as e:
        print(f"Extract node LLM failure: {e}")
        # Fallback to simple logic for resilience
        return {
            "functional_requirements": [
                FunctionalRequirement(id=1, text="The system shall allow users to browse products.", actor="User", goal="browse products", source_hint="browse")
            ],
            "error": f"EXTRACT_LLM_FAILURE: {str(e)}"
        }
