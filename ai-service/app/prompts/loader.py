import logging
from functools import lru_cache
from app.prompts.registry import PromptId, get_template_path

logger = logging.getLogger(__name__)

@lru_cache(maxsize=32)
def load_prompt(prompt_id: PromptId) -> str:
    """
    Load a prompt template from disk and cache it.
    Uses UTF-8 encoding.
    Raises FileNotFoundError if the template is missing.
    """
    path = get_template_path(prompt_id)
    
    if not path.exists():
        error_msg = f"Prompt template not found at {path} for ID {prompt_id}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            
        if not content:
            logger.warning(f"Prompt template at {path} is empty.")
            
        return content
    except Exception as e:
        logger.error(f"Failed to load prompt template {prompt_id} from {path}: {e}")
        raise e
