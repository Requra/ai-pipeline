import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

class Settings:
    ENV: str = os.getenv("ENV", "development")
    
    # Provider Keys
    GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY")
    GROQ_API_KEY: Optional[str] = os.getenv("GROQ_API_KEY")
    DEEPGRAM_API_KEY: Optional[str] = os.getenv("DEEPGRAM_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    # Default OpenAI model for LLM reasoning nodes
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # Testing / OSS Keys
    GPT_OSS_API_KEY: Optional[str] = os.getenv("GPT_OSS_API_KEY")
    BASE_URL_KEY: Optional[str] = os.getenv("BASE_URL_KEY")
    
    # LLM Settings
    # NOTE: For the MVP, OpenAI is the single LLM reasoning provider. This
    # setting remains for backward compatibility but is ignored by the runtime.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").lower().strip()
    
    # Transcribe Settings
    TRANSCRIBE_PROVIDER: str = os.getenv("TRANSCRIBE_PROVIDER", "groq").lower().strip()
    GROQ_WHISPER_MODEL: str = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    GROQ_LANGUAGE: Optional[str] = os.getenv("GROQ_LANGUAGE")

settings = Settings()
