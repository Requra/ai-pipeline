from typing import Optional
from app.config import settings


def get_llm(model_name: Optional[str] = None):
    """Return a chat LLM client for reasoning nodes.

    Behavior:
    - If `settings.LLM_PROVIDER` == 'groq', try to return a Groq-backed Chat model
      (requires `langchain_groq` to be installed and `GROQ_API_KEY` set).
    - Otherwise return the OpenAI-backed Chat client.
    """
    # Select model based on provider
    if settings.LLM_PROVIDER == "groq":
        model = model_name or settings.GROQ_MODEL
    else:
        model = model_name or settings.OPENAI_MODEL or "gpt-4o-mini"

    # Groq provider
    if settings.LLM_PROVIDER == "groq":
        # Use the local Groq adapter which talks to the Groq REST API via httpx.
        try:
            from app.llm_adapters.groq_adapter import GroqChat

            return GroqChat(model=model, temperature=0, api_key=settings.GROQ_API_KEY)
        except Exception as e:
            raise RuntimeError(
                "LLM_PROVIDER is set to 'groq' but the local Groq adapter failed to initialize."
            ) from e

    # Default: OpenAI via langchain-openai
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        temperature=0,
        openai_api_key=settings.OPENAI_API_KEY,
    )
