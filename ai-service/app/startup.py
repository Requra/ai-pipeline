import shutil
import logging
import sys
from app.config import settings

# Configure logging to ensure startup messages are visible
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def validate_dependencies():
    """
    Verify that all required Python packages and system utilities are available.
    """
    # Check Python packages
    required_packages = {
        "fitz": "pymupdf",
        "docx": "python-docx",
        "groq": "groq",
        "httpx": "httpx",
        "pydub": "pydub"
    }
    
    missing_packages = []
    for module, package in required_packages.items():
        try:
            __import__(module)
        except ImportError:
            missing_packages.append(package)
            
    if missing_packages:
        error_msg = f"Missing required Python packages: {', '.join(missing_packages)}"
        if settings.ENV == "production":
            logger.error(f"CRITICAL: {error_msg}")
            sys.exit(1)
        else:
            logger.warning(f"WARN: {error_msg}")

    # Check system dependencies (required for transcription node)
    for cmd in ["ffmpeg", "ffprobe"]:
        if not shutil.which(cmd):
            error_msg = f"System dependency '{cmd}' not found on PATH. Audio processing will fail."
            if settings.ENV == "production":
                logger.error(f"CRITICAL: {error_msg}")
                sys.exit(1)
            else:
                logger.warning(f"WARN: {error_msg}")

def validate_environment():
    """
    Verify that required environment variables are set based on active providers.
    """
    # 1. Validate LLM Provider
    allowed_llm_providers = {"openai", "gemini"}
    llm_provider = settings.LLM_PROVIDER
    
    if llm_provider not in allowed_llm_providers:
        error_msg = f"Invalid LLM_PROVIDER '{settings.LLM_PROVIDER}'. Allowed values are: 'openai', 'gemini'."
        if settings.ENV == "production":
            logger.error(f"CRITICAL: {error_msg}")
            sys.exit(1)
        else:
            logger.warning(f"WARN: {error_msg}")
            
    # Check corresponding key based on provider
    if llm_provider == "openai":
        if not settings.OPENAI_API_KEY:
            error_msg = "OPENAI_API_KEY is missing. It is required when LLM_PROVIDER is set to 'openai'."
            if settings.ENV == "production":
                logger.error(f"CRITICAL: {error_msg}")
                sys.exit(1)
            else:
                logger.warning(f"WARN: {error_msg}")
    elif llm_provider == "gemini":
        if not settings.GOOGLE_API_KEY:
            error_msg = "GOOGLE_API_KEY is missing. It is required when LLM_PROVIDER is set to 'gemini'."
            if settings.ENV == "production":
                logger.error(f"CRITICAL: {error_msg}")
                sys.exit(1)
            else:
                logger.warning(f"WARN: {error_msg}")

    # 2. Validate Transcription Provider
    allowed_transcribe_providers = {"groq", "deepgram"}
    transcribe_provider = settings.TRANSCRIBE_PROVIDER
    
    if transcribe_provider not in allowed_transcribe_providers:
        error_msg = f"Invalid TRANSCRIBE_PROVIDER '{settings.TRANSCRIBE_PROVIDER}'. Allowed values are: 'groq', 'deepgram'."
        if settings.ENV == "production":
            logger.error(f"CRITICAL: {error_msg}")
            sys.exit(1)
        else:
            logger.warning(f"WARN: {error_msg}")

    # Validate Transcription Provider Keys
    if transcribe_provider == "groq":
        if not settings.GROQ_API_KEY:
            error_msg = "TRANSCRIBE_PROVIDER is set to 'groq', but GROQ_API_KEY is missing."
            if settings.ENV == "production":
                logger.error(f"CRITICAL: {error_msg}")
                sys.exit(1)
            else:
                logger.warning(f"WARN: {error_msg}")
    elif transcribe_provider == "deepgram":
        if not settings.DEEPGRAM_API_KEY:
            error_msg = "TRANSCRIBE_PROVIDER is set to 'deepgram', but DEEPGRAM_API_KEY is missing."
            if settings.ENV == "production":
                logger.error(f"CRITICAL: {error_msg}")
                sys.exit(1)
            else:
                logger.warning(f"WARN: {error_msg}")

def run_startup_checks():
    """
    Run all Phase 1 startup validation checks.
    """
    logger.info(f"--- STARTUP VALIDATION (ENV={settings.ENV}) ---")
    validate_dependencies()
    validate_environment()
    logger.info("--- STARTUP VALIDATION COMPLETED ---")
