import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    ENV: str = os.getenv("ENV", "development")

    # When true (and ENV != production) nodes may log raw LLM input/output at
    # DEBUG for local debugging. Forced off in production so raw document text,
    # full prompts, and full model responses never reach production logs.
    DEBUG_LLM_IO: bool = _env_flag("DEBUG_LLM_IO")

    # Provider Keys
    GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY")
    GROQ_API_KEY: Optional[str] = os.getenv("GROQ_API_KEY")
    DEEPGRAM_API_KEY: Optional[str] = os.getenv("DEEPGRAM_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    # Default OpenAI model for LLM reasoning nodes
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # OpenRouter Settings
    OPENROUTER_API_KEY: Optional[str] = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    OPENROUTER_APP_URL: Optional[str] = os.getenv("OPENROUTER_APP_URL")
    OPENROUTER_APP_NAME: str = os.getenv("OPENROUTER_APP_NAME", "Requra AI Pipeline")
    
    # Testing / OSS Keys
    GPT_OSS_API_KEY: Optional[str] = os.getenv("GPT_OSS_API_KEY")
    BASE_URL_KEY: Optional[str] = os.getenv("BASE_URL_KEY")
    
    # LLM Settings
    # Default reasoning provider. Can be 'openrouter', 'openai', or 'groq'.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter").lower().strip()
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Transcribe Settings
    TRANSCRIBE_PROVIDER: str = os.getenv("TRANSCRIBE_PROVIDER", "groq").lower().strip()
    GROQ_WHISPER_MODEL: str = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    GROQ_LANGUAGE: Optional[str] = os.getenv("GROQ_LANGUAGE")

settings = Settings()
