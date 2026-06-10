from typing import Optional
import importlib
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
    elif settings.LLM_PROVIDER == "gemini":
        model = model_name or settings.GEMINI_MODEL
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

    # Gemini provider (LangGraph integration)
    if settings.LLM_PROVIDER == "gemini":
        try:
            # Try common LangGraph Gemini package names
            candidates = [
                "langgraph_gemini",
                "langgraph.gemini",
                "langgraph.adapters.gemini",
                "langgraph.clients.gemini",
            ]
            mod = None
            for name in candidates:
                try:
                    mod = importlib.import_module(name)
                    break
                except Exception:
                    mod = None
            if mod is None:
                raise RuntimeError(
                    "LangGraph Gemini package not found. Install the LangGraph Gemini integration (e.g. 'langgraph-gemini') and ensure it's on PYTHONPATH."
                )

            # Look for a reasonable client/class factory
            client_cls = None
            for attr in ("GeminiClient", "Gemini", "GeminiLLM", "LangGraphGeminiClient", "Client"):
                if hasattr(mod, attr):
                    client_cls = getattr(mod, attr)
                    break

            if client_cls is not None:
                # Instantiate the client with api key and model when possible
                try:
                    return client_cls(api_key=settings.GEMINI_API_KEY, model=model, temperature=0)
                except TypeError:
                    return client_cls(settings.GEMINI_API_KEY, model)
            # Try a common factory function
            if hasattr(mod, "create_client"):
                return mod.create_client(api_key=settings.GEMINI_API_KEY, model=model)

            raise RuntimeError("Could not initialize LangGraph Gemini client from the installed package.")
        except Exception as e:
            raise RuntimeError(
                "LLM_PROVIDER is set to 'gemini' but the LangGraph Gemini client failed to initialize."
            ) from e

    # Default: OpenAI via langchain-openai
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        temperature=0,
    )
