import pytest
from app.prompts.registry import PromptId, PROMPT_MAP, get_template_path

def test_every_prompt_id_is_mapped():
    """Verify that every PromptId enum member has a corresponding entry in PROMPT_MAP."""
    for prompt_id in PromptId:
        assert prompt_id in PROMPT_MAP, f"PromptId {prompt_id} is missing from PROMPT_MAP"

def test_get_template_path_returns_valid_path():
    """Verify that get_template_path returns a Path object for every PromptId."""
    for prompt_id in PromptId:
        path = get_template_path(prompt_id)
        assert path.name == PROMPT_MAP[prompt_id]
        assert "templates" in str(path)

def test_get_template_path_invalid_id():
    """Verify that get_template_path raises ValueError for an unmapped ID."""
    with pytest.raises(ValueError, match="No template mapped"):
        # We bypass the Enum type check for this test
        get_template_path("NON_EXISTENT_ID")
