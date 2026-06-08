from typing import Optional
from app.config import settings

def get_llm(model_name: Optional[str] = None):
    """Return an OpenAI-backed Chat client for all LLM reasoning nodes.

    For the MVP we standardize on OpenAI as the single provider for reasoning.
    The model can be overridden via `model_name` or the `OPENAI_MODEL` setting.
    """
    # Import here to avoid importing heavy providers at module import time in tests
    from langchain_openai import ChatOpenAI

    model = model_name or settings.OPENAI_MODEL or "gpt-4o-mini"
    return ChatOpenAI(
        model=model,
        temperature=0,
        openai_api_key=settings.OPENAI_API_KEY,
    )
