import shutil
import logging
import sys
from typing import Any, Dict, Optional

from app.config import (
    SUPPORTED_LLM_PROVIDERS,
    SUPPORTED_TRANSCRIBE_PROVIDERS,
    collect_config_problems,
    llm_key_for,
    settings,
    transcribe_key_for,
)

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

    Provider support is sourced from ``app.config.SUPPORTED_LLM_PROVIDERS`` so the
    accepted set stays identical across ``llm.get_llm``, this startup check, and
    the ``/ready`` readiness probe. In particular ``groq`` is a first-class LLM
    provider in all three places (previously this check rejected it).
    """
    # 1. Validate LLM keys
    provider = settings.LLM_PROVIDER

    if provider not in SUPPORTED_LLM_PROVIDERS:
        error_msg = (
            f"Unsupported LLM_PROVIDER: {provider}. "
            f"Supported providers: {', '.join(sorted(SUPPORTED_LLM_PROVIDERS))}"
        )
        if settings.ENV == "production":
            logger.error(f"CRITICAL: {error_msg}")
            sys.exit(1)
        else:
            logger.warning(f"WARN: {error_msg}")
    elif not llm_key_for(provider):
        error_msg = f"LLM_PROVIDER is set to '{provider}', but its API key is missing."
        if settings.ENV == "production":
            logger.error(f"CRITICAL: {error_msg}")
            sys.exit(1)
        else:
            logger.warning(f"WARN: {error_msg}")

    # 2. Validate Transcription Provider
    allowed_transcribe_providers = SUPPORTED_TRANSCRIBE_PROVIDERS
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
    Run all startup validation checks.

    In production, any production-critical configuration problem (from
    ``collect_config_problems``) aborts startup via ``RuntimeError`` — fail fast
    rather than serve traffic with a broken configuration. In non-production the
    same problems are logged as warnings only.
    """
    logger.info(f"--- STARTUP VALIDATION (ENV={settings.ENV}) ---")
    validate_dependencies()
    validate_environment()

    problems = collect_config_problems()
    if problems:
        if settings.is_production:
            for problem in problems:
                logger.error(f"CRITICAL: {problem}")
            # Raise (not sys.exit) so uvicorn surfaces the reason clearly and
            # tests can assert on it without killing the interpreter.
            raise RuntimeError(
                "Startup aborted — invalid production configuration:\n- "
                + "\n- ".join(problems)
            )
        for problem in problems:
            logger.warning(f"WARN: {problem}")

    logger.info("--- STARTUP VALIDATION COMPLETED ---")


# ---------------------------------------------------------------------------
# Readiness diagnostics (safe to expose via GET /ready)
# ---------------------------------------------------------------------------

# Provider support sets live in app.config so they stay consistent across
# get_llm(), startup validation, and this readiness probe.
_SUPPORTED_LLM_PROVIDERS = SUPPORTED_LLM_PROVIDERS
_SUPPORTED_TRANSCRIBE_PROVIDERS = SUPPORTED_TRANSCRIBE_PROVIDERS

_llm_key_for = llm_key_for
_transcribe_key_for = transcribe_key_for


async def build_readiness_report() -> Dict[str, Any]:
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
    # Audio is a hard requirement only when ENABLE_AUDIO is set.
    audio_enabled = _flag(getattr(settings, "ENABLE_AUDIO", False))
    transcribe_ok = transcribe_supported and (transcribe_key_present or not audio_enabled)

    # Infrastructure probes — all fully guarded, safe booleans only.
    db_check = await _probe_database()
    queue_check = _probe_redis()
    pgvector_check = _probe_pgvector()
    embeddings_check = _probe_embeddings()
    auth_configured = bool(getattr(settings, "AI_INTERNAL_SERVICE_TOKEN", None))
    origins_ok = _origins_configured()
    is_prod = _flag(getattr(settings, "is_production", False))

    # Readiness gate: LLM always; in production also require durable store,
    # a configured internal token + CORS origins, and any configured queue.
    ready = llm_ok
    if is_prod:
        ready = (
            ready
            and db_check["ok"]
            and queue_check["ok"]
            and auth_configured
            and origins_ok
            and (transcribe_ok)
            and (embeddings_check["ok"])
        )

    report: Dict[str, Any] = {
        "ready": bool(ready),
        "env": settings.ENV,
        "checks": {
            "llm": {
                "ok": llm_ok,
                "provider": llm_provider,
                "provider_supported": llm_supported,
                "api_key_present": llm_key_present,
            },
            "transcription": {
                "ok": transcribe_ok,
                "provider": transcribe_provider,
                "provider_supported": transcribe_supported,
                "api_key_present": transcribe_key_present,
                "required": audio_enabled,
                "optional": not audio_enabled,
            },
            "database": db_check,
            "queue": queue_check,
            "pgvector": pgvector_check,
            "embeddings": embeddings_check,
            "internal_auth": {"configured": auth_configured},
            "cors": {"ok": origins_ok, "required": is_prod},
        },
    }
    return report


# ---------------------------------------------------------------------------
# Guarded infrastructure probes (never raise; never leak secrets)
# ---------------------------------------------------------------------------

def _flag(value: Any) -> bool:
    """Coerce a possibly-Mock/None value to a plain bool defensively."""
    try:
        return bool(value) if isinstance(value, (bool, int, str)) else False
    except Exception:
        return False


def _origins_configured() -> bool:
    try:
        origins = settings.allowed_origins
        return isinstance(origins, list) and len(origins) > 0
    except Exception:
        return False


async def _probe_database() -> Dict[str, Any]:
    url = getattr(settings, "DATABASE_URL", None)
    is_prod = _flag(getattr(settings, "is_production", False))
    if not isinstance(url, str) or not url:
        # In production, DB is required; in dev, in-memory store is permitted.
        return {"ok": not is_prod, "configured": False, "durable": False}
    try:
        from app.store.db import get_database

        db = get_database(url)
        await db.ping()
        schema_present = await _probe_schema(db)
        return {"ok": True, "configured": True, "durable": True, "schema_present": schema_present}
    except Exception as exc:
        logger.warning("readiness: database probe failed: %s", type(exc).__name__)
        return {"ok": False, "configured": True, "durable": True, "error": type(exc).__name__}


async def _probe_schema(db) -> bool:
    try:
        from sqlalchemy import text

        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM ai_jobs LIMIT 1"))
        return True
    except Exception:
        return False


def _probe_redis() -> Dict[str, Any]:
    url = getattr(settings, "REDIS_URL", None)
    is_prod = _flag(getattr(settings, "is_production", False))
    allow_inprocess = _flag(getattr(settings, "ALLOW_INPROCESS_QUEUE_IN_PRODUCTION", False))
    if not isinstance(url, str) or not url:
        ok = not is_prod or allow_inprocess
        return {"ok": ok, "configured": False, "backend": "in-process"}
    try:
        from app.queue.redis_queue import get_redis_connection

        ok = bool(get_redis_connection(url).ping())
        return {"ok": ok, "configured": True, "backend": "redis"}
    except Exception as exc:
        logger.warning("readiness: redis probe failed: %s", type(exc).__name__)
        if allow_inprocess:
            return {"ok": True, "configured": True, "backend": "in-process", "warning": f"Redis unreachable ({type(exc).__name__}), falling back to in-process"}
        return {"ok": False, "configured": True, "backend": "redis", "error": type(exc).__name__}


def _probe_pgvector() -> Dict[str, Any]:
    enabled = _flag(getattr(settings, "ENABLE_EMBEDDINGS", False))
    try:
        import pgvector  # noqa: F401

        installed = True
    except Exception:
        installed = False
    # Only a hard requirement when embeddings are enabled.
    return {"ok": (installed or not enabled), "python_package_installed": installed, "required": enabled}


def _probe_embeddings() -> Dict[str, Any]:
    enabled = _flag(getattr(settings, "ENABLE_EMBEDDINGS", False))
    if not enabled:
        return {"ok": True, "enabled": False}
    provider = getattr(settings, "EMBEDDING_PROVIDER", "openai")
    from app.config import embedding_key_for

    key_present = bool(embedding_key_for(provider))
    return {"ok": key_present, "enabled": True, "provider": provider, "api_key_present": key_present}
