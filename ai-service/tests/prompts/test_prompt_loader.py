import pytest
from app.prompts.registry import PromptId
from app.prompts.loader import load_prompt

def test_every_registered_prompt_exists_and_loads():
    """Verify that every prompt in the registry can be loaded from disk."""
    for prompt_id in PromptId:
        content = load_prompt(prompt_id)
        assert content, f"Prompt {prompt_id} is empty"
        assert isinstance(content, str)

def test_load_prompt_caching():
    """Verify that load_prompt caches the content."""
    # This is a bit indirect but we can check if it returns the same object
    for prompt_id in PromptId:
        content1 = load_prompt(prompt_id)
        content2 = load_prompt(prompt_id)
        assert content1 is content2, f"Prompt {prompt_id} was not cached"

def test_load_prompt_missing_file(monkeypatch):
    """Verify that load_prompt raises FileNotFoundError for missing files."""
    from pathlib import Path
    import app.prompts.loader
    
    # Mock get_template_path to return a non-existent file
    monkeypatch.setattr(app.prompts.loader, "get_template_path", lambda x: Path("non_existent_file.md"))
    
    # We must clear the cache if we used it before in other tests
    app.prompts.loader.load_prompt.cache_clear()
    
    with pytest.raises(FileNotFoundError, match="Prompt template not found"):
        load_prompt(PromptId.INGEST_RELEVANCE_V1)

def test_prompts_are_utf8_readable():
    """Verify that all prompt files are readable as UTF-8."""
    for prompt_id in PromptId:
        # load_prompt already uses utf-8
        content = load_prompt(prompt_id)
        # If it didn't raise UnicodeDecodeError, it's fine
        assert content is not None
