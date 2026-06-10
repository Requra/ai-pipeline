from langchain_openai import ChatOpenAI
from app.config import settings

def get_openrouter_llm():
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is missing")

    default_headers = {}

    if settings.OPENROUTER_APP_URL:
        default_headers["HTTP-Referer"] = settings.OPENROUTER_APP_URL

    if settings.OPENROUTER_APP_NAME:
        default_headers["X-OpenRouter-Title"] = settings.OPENROUTER_APP_NAME

    return ChatOpenAI(
        model=settings.OPENROUTER_MODEL,
        temperature=0,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        default_headers=default_headers or None,
    )

def get_openai_llm():
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        temperature=0,
        api_key=settings.OPENAI_API_KEY,
    )

def get_llm():
    provider = (settings.LLM_PROVIDER or "openrouter").lower()

    if provider == "openrouter":
        return get_openrouter_llm()

    if provider == "openai":
        return get_openai_llm()

    raise RuntimeError(
        f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}. "
        "Supported providers: openrouter, openai"
    )
