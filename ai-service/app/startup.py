import shutil
import logging
import sys
from typing import Any, Dict, Optional

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
    # 1. Validate LLM keys
    provider = settings.LLM_PROVIDER
    
    if provider == "openrouter":
        if not settings.OPENROUTER_API_KEY:
            error_msg = "LLM_PROVIDER is set to 'openrouter', but OPENROUTER_API_KEY is missing."
            if settings.ENV == "production":
                logger.error(f"CRITICAL: {error_msg}")
                sys.exit(1)
            else:
                logger.warning(f"WARN: {error_msg}")
    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            error_msg = "LLM_PROVIDER is set to 'openai', but OPENAI_API_KEY is missing."
            if settings.ENV == "production":
                logger.error(f"CRITICAL: {error_msg}")
                sys.exit(1)
            else:
                logger.warning(f"WARN: {error_msg}")
    else:
        error_msg = f"Unsupported LLM_PROVIDER: {provider}. Supported providers: openrouter, openai"
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


# ---------------------------------------------------------------------------
# Readiness diagnostics (safe to expose via GET /ready)
# ---------------------------------------------------------------------------

_SUPPORTED_LLM_PROVIDERS = {"openrouter", "openai", "groq"}
_SUPPORTED_TRANSCRIBE_PROVIDERS = {"groq", "deepgram"}


def _llm_key_for(provider: str) -> Optional[str]:
    return {
        "openrouter": settings.OPENROUTER_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "groq": settings.GROQ_API_KEY,
    }.get(provider)


def _transcribe_key_for(provider: str) -> Optional[str]:
    return {
        "groq": settings.GROQ_API_KEY,
        "deepgram": settings.DEEPGRAM_API_KEY,
    }.get(provider)


def build_readiness_report() -> Dict[str, Any]:
    """Return safe readiness diagnostics — booleans and provider names only.

    Never returns API keys or any secret material. The LLM check is the hard
    gate for readiness; transcription is reported but treated as a soft/optional
    capability (audio is opt-in), so a missing transcription key does not by
    itself make the service "not ready".
    """
    llm_provider = settings.LLM_PROVIDER
    llm_supported = llm_provider in _SUPPORTED_LLM_PROVIDERS
    llm_key_present = bool(_llm_key_for(llm_provider))
    llm_ok = llm_supported and llm_key_present

    transcribe_provider = settings.TRANSCRIBE_PROVIDER
    transcribe_supported = transcribe_provider in _SUPPORTED_TRANSCRIBE_PROVIDERS
    transcribe_key_present = bool(_transcribe_key_for(transcribe_provider))

    report: Dict[str, Any] = {
        "ready": llm_ok,
        "env": settings.ENV,
        "checks": {
            "llm": {
                "ok": llm_ok,
                "provider": llm_provider,
                "provider_supported": llm_supported,
                "api_key_present": llm_key_present,
            },
            "transcription": {
                # Soft capability: valid config = ok; missing key is reported,
                # not fatal, because audio input is optional.
                "ok": transcribe_supported,
                "provider": transcribe_provider,
                "provider_supported": transcribe_supported,
                "api_key_present": transcribe_key_present,
                "optional": True,
            },
        },
    }
    return report
