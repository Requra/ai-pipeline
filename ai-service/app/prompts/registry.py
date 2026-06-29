from enum import Enum
from pathlib import Path

class PromptId(str, Enum):
    INGEST_RELEVANCE_V1 = "ingest_relevance_v1"
    EXTRACT_REQUIREMENTS_V1 = "extract_requirements_v1"
    EXTRACT_REQUIREMENTS_V2 = "extract_requirements_v2"
    CLASSIFY_REQUIREMENTS_V1 = "classify_requirements_v1"
    GENERATE_USER_STORIES_V1 = "generate_user_stories_v1"
    SUMMARIZE_STRUCTURED_V1 = "summarize_structured_v1"

# Map PromptId to template filename
PROMPT_MAP = {
    PromptId.INGEST_RELEVANCE_V1: "ingest_relevance_v1.md",
    PromptId.EXTRACT_REQUIREMENTS_V1: "extract_requirements_v1.md",
    PromptId.EXTRACT_REQUIREMENTS_V2: "extract_requirements_v2.md",
    PromptId.CLASSIFY_REQUIREMENTS_V1: "classify_requirements_v1.md",
    PromptId.GENERATE_USER_STORIES_V1: "generate_user_stories_v1.md",
    PromptId.SUMMARIZE_STRUCTURED_V1: "summarize_structured_v1.md",
}

def get_template_path(prompt_id: PromptId) -> Path:
    """Get the absolute path to a prompt template."""
    base_path = Path(__file__).parent / "templates"
    filename = PROMPT_MAP.get(prompt_id)
    if not filename:
        raise ValueError(f"No template mapped for prompt ID: {prompt_id}")
    return base_path / filename
