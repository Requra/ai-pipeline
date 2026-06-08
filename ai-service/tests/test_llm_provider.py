import sys
import types
from app.config import settings


def _inject_fake_langchain_openai():
    """Insert a fake `langchain_openai` module exposing `ChatOpenAI`.

    This avoids importing the real package (and its tiktoken native deps)
    during tests while allowing us to validate `get_llm()` behavior.
    """
    class FakeChatOpenAI:
        def __init__(self, model=None, temperature=0, openai_api_key=None, **kwargs):
            self.model = model
            self.temperature = temperature
            self.openai_api_key = openai_api_key

    fake_mod = types.ModuleType("langchain_openai")
    setattr(fake_mod, "ChatOpenAI", FakeChatOpenAI)
    sys.modules["langchain_openai"] = fake_mod


def test_get_llm_default_model(monkeypatch):
    _inject_fake_langchain_openai()
    # Ensure defaults don't call external APIs; just construct the client
    monkeypatch.setattr(settings, "OPENAI_MODEL", None)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    from app.llm import get_llm

    llm = get_llm()
    # FakeChatOpenAI was injected; ensure an instance was returned
    assert hasattr(llm, "model")
    model_attr = getattr(llm, "model", None)
    assert model_attr == "gpt-4o-mini"


def test_get_llm_custom_model(monkeypatch):
    _inject_fake_langchain_openai()
    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-test-model")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    from app.llm import get_llm

    llm = get_llm()
    assert hasattr(llm, "model")
    model_attr = getattr(llm, "model", None)
    assert model_attr == "gpt-test-model"
