import sys
import types
import pytest
from app.config import settings


def _inject_fake_langchain_openai():
    """Insert a fake `langchain_openai` module exposing `ChatOpenAI`.
    """
    class FakeChatOpenAI:
        def __init__(self, model=None, temperature=0, api_key=None, base_url=None, default_headers=None, **kwargs):
            # Real ChatOpenAI uses model_name internally often, but we can just use self.model
            self.model = model
            self.temperature = temperature
            self.api_key = api_key
            self.base_url = base_url
            self.default_headers = default_headers
            self.kwargs = kwargs

    fake_mod = types.ModuleType("langchain_openai")
    setattr(fake_mod, "ChatOpenAI", FakeChatOpenAI)
    sys.modules["langchain_openai"] = fake_mod
    
    # Also need to clear app.llm from sys.modules to force re-import with fake
    if "app.llm" in sys.modules:
        del sys.modules["app.llm"]



def test_get_llm_openrouter(monkeypatch):
    _inject_fake_langchain_openai()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(settings, "OPENROUTER_MODEL", "test-model")
    monkeypatch.setattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.test/api/v1")
    monkeypatch.setattr(settings, "OPENROUTER_APP_NAME", "Test App")

    from app.llm import get_llm

    llm = get_llm()
    provider_client = llm._instantiate_client("openrouter", "test-model")
    assert llm.providers == [{"provider": "openrouter", "model": "test-model"}]
    assert provider_client.model == "test-model"
    assert provider_client.api_key == "test-openrouter-key"
    assert provider_client.base_url == "https://openrouter.test/api/v1"
    assert provider_client.default_headers["X-OpenRouter-Title"] == "Test App"
    assert provider_client.kwargs["timeout"] == settings.PROVIDER_TIMEOUT_SECONDS
    assert provider_client.kwargs["max_retries"] == 0


def test_get_llm_openai(monkeypatch):
    _inject_fake_langchain_openai()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    from app.llm import get_llm

    llm = get_llm()
    provider_client = llm._instantiate_client("openai", "gpt-4o-mini")
    assert llm.providers == [{"provider": "openai", "model": "gpt-4o-mini"}]
    assert provider_client.model == "gpt-4o-mini"
    assert provider_client.api_key == "test-openai-key"


def test_get_llm_groq(monkeypatch):
    _inject_fake_langchain_openai()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")

    from app.llm import get_llm

    llm = get_llm()
    provider_client = llm._instantiate_client("groq", "llama-3.3-70b-versatile")
    assert llm.providers == [{"provider": "groq", "model": "llama-3.3-70b-versatile"}]
    assert provider_client.model == "llama-3.3-70b-versatile"
    assert provider_client.api_key == "test-groq-key"
    assert provider_client.base_url == "https://api.groq.com/openai/v1"


def test_get_llm_unsupported(monkeypatch):
    _inject_fake_langchain_openai()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "invalid_provider")

    from app.llm import get_llm

    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER: invalid_provider"):
        get_llm()

