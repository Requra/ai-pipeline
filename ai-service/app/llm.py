from typing import Optional
import importlib
from app.config import settings


def get_llm(model_name: Optional[str] = None):
    """Return a chat LLM client for reasoning nodes.

    Behavior:
    - If `settings.LLM_PROVIDER` == 'openrouter', return an OpenAI-backed Chat client configured for OpenRouter.
    - If `settings.LLM_PROVIDER` == 'openai', return a standard OpenAI-backed Chat client.
    - If `settings.LLM_PROVIDER` == 'groq', return an OpenAI-backed Chat client configured for Groq.
    - Otherwise, raise RuntimeError.
    """
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider not in ("openrouter", "openai", "groq"):
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")

    from langchain_openai import ChatOpenAI

    if provider == "openrouter":
        model = model_name or settings.OPENROUTER_MODEL or "openai/gpt-4o-mini"
        extra_headers = {}
        if settings.OPENROUTER_APP_NAME:
            extra_headers["X-OpenRouter-Title"] = settings.OPENROUTER_APP_NAME
        if settings.OPENROUTER_APP_URL:
            extra_headers["HTTP-Referer"] = settings.OPENROUTER_APP_URL

        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            default_headers=extra_headers if extra_headers else None,
        )
    elif provider == "groq":
        model = model_name or settings.GROQ_MODEL or "llama-3.3-70b-versatile"
        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
    else:  # openai
        model = model_name or settings.OPENAI_MODEL or "gpt-4o-mini"
        return ChatOpenAI(
            model=model,
            temperature=0,
            api_key=settings.OPENAI_API_KEY,
        )
