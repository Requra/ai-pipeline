"""
Typed configuration for the AI pipeline service.

Two layers live here:

  * The historical ``Settings`` fields (LLM/transcription providers + keys) that
    the MVP pipeline nodes already read. These are kept byte-for-byte compatible.
  * The production layer (database, redis/queue, embeddings, backend
    service-to-service auth + callback, timeouts, retention). These are additive.

Rules honoured here:
  * Never log secret *values* — the readiness/diagnostics layer only ever reports
    presence booleans and provider names (see ``app.startup``).
  * In production (``ENV in {production, prod}``) required configuration must be
    present or the process fails fast (see ``validate_required_config``).
  * ``DEBUG_LLM_IO`` is force-disabled in production regardless of the env value.
"""

from __future__ import annotations

import os
import logging
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

MVP_ENABLE_CONFLICT_DETECTION_DEFAULT = "true"
MVP_ENABLE_QUALITY_REPAIR_DEFAULT = "true"

# Load environment variables from .env if present
load_dotenv()


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _split_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    ENV: str = os.getenv("ENV", "development")

    # ------------------------------------------------------------------
    # Observability / safety
    # ------------------------------------------------------------------
    # When true (and ENV != production) nodes may log raw LLM input/output at
    # DEBUG for local debugging. Forced off in production so raw document text,
    # full prompts, and full model responses never reach production logs. Access
    # via `settings.debug_llm_io_enabled` which enforces the production override.
    DEBUG_LLM_IO: bool = _env_flag("DEBUG_LLM_IO")
    ENABLE_PII_MASKING: bool = _env_flag("ENABLE_PII_MASKING", "true")

    # Comma-separated browser origins for CORS (direct-demo endpoints).
    ALLOWED_ORIGINS_RAW: Optional[str] = os.getenv("ALLOWED_ORIGINS")

    # ------------------------------------------------------------------
    # Provider Keys (LLM reasoning)
    # ------------------------------------------------------------------
    GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY")
    GROQ_API_KEY: Optional[str] = os.getenv("GROQ_API_KEY")
    DEEPGRAM_API_KEY: Optional[str] = os.getenv("DEEPGRAM_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
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

    # LLM Settings — default reasoning provider: 'openrouter', 'openai', or 'groq'.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter").lower().strip()
    LLM_FALLBACK_CHAIN: Optional[str] = os.getenv("LLM_FALLBACK_CHAIN")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    # Transcribe Settings
    TRANSCRIBE_PROVIDER: str = os.getenv("TRANSCRIBE_PROVIDER", "groq").lower().strip()
    GROQ_WHISPER_MODEL: str = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    GROQ_LANGUAGE: Optional[str] = os.getenv("GROQ_LANGUAGE")
    # Whether audio processing is expected in this deployment. When true, /ready
    # treats a missing transcription key as not-ready.
    ENABLE_AUDIO: bool = _env_flag("ENABLE_AUDIO")

    # ------------------------------------------------------------------
    # Service-to-service auth (internal /internal/* endpoints)
    # ------------------------------------------------------------------
    # Bearer token the backend must present to call /internal/* endpoints.
    AI_INTERNAL_SERVICE_TOKEN: Optional[str] = os.getenv("AI_INTERNAL_SERVICE_TOKEN")

    # ------------------------------------------------------------------
    # Database (durable AI processing store) — Postgres + pgvector in prod.
    # When unset, the service falls back to in-memory stores (dev/demo/tests).
    # ------------------------------------------------------------------
    DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

    # ------------------------------------------------------------------
    # Redis / queue (job dispatch + cache only — never source of truth).
    # When unset, the service runs jobs in-process (dev/demo/tests).
    # ------------------------------------------------------------------
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    QUEUE_NAME: str = os.getenv("QUEUE_NAME", "ai_jobs")
    ALLOW_INPROCESS_QUEUE_IN_PRODUCTION: bool = _env_flag("ALLOW_INPROCESS_QUEUE_IN_PRODUCTION", "false")

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    JOB_RESULT_RETENTION_DAYS: int = _env_int("JOB_RESULT_RETENTION_DAYS", 30)
    CHUNK_RETENTION_DAYS: int = _env_int("CHUNK_RETENTION_DAYS", 30)

    # ------------------------------------------------------------------
    # Embeddings (semantic retrieval — opt-in, disabled by default for MVP).
    # ------------------------------------------------------------------
    ENABLE_EMBEDDINGS: bool = _env_flag("ENABLE_EMBEDDINGS")
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "openai").lower().strip()
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_DIMENSIONS: int = _env_int("EMBEDDING_DIMENSIONS", 1536)
    # When true, retrieve_evidence merges BM25 + vector results (hybrid). Vector
    # retrieval only augments recall; final evidence still passes quote
    # verification and grounding (never a source of truth on its own).
    ENABLE_HYBRID_RETRIEVAL: bool = _env_flag("ENABLE_HYBRID_RETRIEVAL")

    # ------------------------------------------------------------------
    # Backend integration (callback + document fetch)
    # ------------------------------------------------------------------
    BACKEND_BASE_URL: Optional[str] = os.getenv("BACKEND_BASE_URL")
    # Token the AI service presents when calling back to the backend.
    BACKEND_SERVICE_TOKEN: Optional[str] = os.getenv("BACKEND_SERVICE_TOKEN")
    CALLBACK_TIMEOUT_SECONDS: int = _env_int("CALLBACK_TIMEOUT_SECONDS", 15)

    # ------------------------------------------------------------------
    # Execution limits
    # ------------------------------------------------------------------
    MAX_JOB_RUNTIME_SECONDS: int = _env_int("MAX_JOB_RUNTIME_SECONDS", 900)
    MAX_CONCURRENT_JOBS: int = _env_int("MAX_CONCURRENT_JOBS", 4)
    # Per provider (LLM/transcription) network call timeout.
    PROVIDER_TIMEOUT_SECONDS: int = _env_int("PROVIDER_TIMEOUT_SECONDS", 120)
    # Shared per-process guard for reasoning-model calls. This limits request
    # bursts from parallel extraction chunks and concurrent jobs without
    # changing pipeline content or the public response contract.
    LLM_MAX_CONCURRENCY: int = _env_int("LLM_MAX_CONCURRENCY", 2)
    # Number of retries after the initial attempt (2 = at most 3 attempts).
    LLM_MAX_RETRIES: int = _env_int("LLM_MAX_RETRIES", 2)
    LLM_RETRY_BASE_SECONDS: float = _env_float("LLM_RETRY_BASE_SECONDS", 1.0)
    LLM_RETRY_MAX_SECONDS: float = _env_float("LLM_RETRY_MAX_SECONDS", 30.0)
    LLM_QUOTA_COOLDOWN_SECONDS: float = _env_float("LLM_QUOTA_COOLDOWN_SECONDS", 300.0)

    # ------------------------------------------------------------------
    # Source processing & operational limits
    # ------------------------------------------------------------------
    SOURCE_PROCESS_CONCURRENCY: int = _env_int("SOURCE_PROCESS_CONCURRENCY", 3)
    STT_CONCURRENCY: int = _env_int("STT_CONCURRENCY", 2)
    MAX_AUDIO_SOURCES_PER_JOB: int = _env_int("MAX_AUDIO_SOURCES_PER_JOB", 3)
    MAX_SOURCES_PER_JOB: int = _env_int("MAX_SOURCES_PER_JOB", 10)
    MAX_DOCUMENT_BYTES: int = _env_int("MAX_DOCUMENT_BYTES", 20 * 1024 * 1024)       # 20 MB
    MAX_AUDIO_BYTES: int = _env_int("MAX_AUDIO_BYTES", 50 * 1024 * 1024)             # 50 MB
    MAX_TOTAL_UPLOAD_BYTES: int = _env_int("MAX_TOTAL_UPLOAD_BYTES", 100 * 1024 * 1024) # 100 MB
    MAX_AUDIO_DURATION_SECONDS: int = _env_int("MAX_AUDIO_DURATION_SECONDS", 1800)   # 30 minutes per audio
    MAX_TOTAL_AUDIO_DURATION_SECONDS: int = _env_int("MAX_TOTAL_AUDIO_DURATION_SECONDS", 5400) # 90 minutes aggregate
    INPUT_CACHE_TTL_SECONDS: int = _env_int("INPUT_CACHE_TTL_SECONDS", 86400)         # 24 hours
    ENABLE_MIXED_SOURCE_JOBS: bool = _env_flag("ENABLE_MIXED_SOURCE_JOBS", "true")

    # ------------------------------------------------------------------
    # Database Connection Pool
    # ------------------------------------------------------------------
    DB_POOL_SIZE: int = _env_int("DB_POOL_SIZE", 5)
    DB_MAX_OVERFLOW: int = _env_int("DB_MAX_OVERFLOW", 10)
    DB_POOL_TIMEOUT_SECONDS: int = _env_int("DB_POOL_TIMEOUT_SECONDS", 30)
    DB_POOL_RECYCLE_SECONDS: int = _env_int("DB_POOL_RECYCLE_SECONDS", 1800)

    # ------------------------------------------------------------------
    # Conflict Detection
    # ------------------------------------------------------------------
    ENABLE_CONFLICT_DETECTION: bool = _env_flag(
        "ENABLE_CONFLICT_DETECTION",
        MVP_ENABLE_CONFLICT_DETECTION_DEFAULT,
    )
    CONFLICT_MIN_CONFIDENCE: float = float(os.getenv("CONFLICT_MIN_CONFIDENCE", "0.80"))
    CONFLICT_SIMILARITY_THRESHOLD: float = float(os.getenv("CONFLICT_SIMILARITY_THRESHOLD", "0.55"))
    CONFLICT_TOP_K: int = _env_int("CONFLICT_TOP_K", 5)
    CONFLICT_JACCARD_LOW: float = float(os.getenv("CONFLICT_JACCARD_LOW", "0.30"))
    CONFLICT_JACCARD_HIGH: float = float(os.getenv("CONFLICT_JACCARD_HIGH", "0.80"))

    # ------------------------------------------------------------------
    # Quality Repair
    # ------------------------------------------------------------------
    ENABLE_QUALITY_REPAIR: bool = _env_flag(
        "ENABLE_QUALITY_REPAIR",
        MVP_ENABLE_QUALITY_REPAIR_DEFAULT,
    )
    MAX_REPAIR_ATTEMPTS: int = _env_int("MAX_REPAIR_ATTEMPTS", 1)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.ENV.strip().lower() in {"production", "prod"}

    @property
    def debug_llm_io_enabled(self) -> bool:
        """DEBUG_LLM_IO with the production hard-override applied."""
        if self.is_production:
            return False
        return self.DEBUG_LLM_IO

    @property
    def use_database(self) -> bool:
        return bool(self.DATABASE_URL)

    @property
    def use_redis_queue(self) -> bool:
        return bool(self.REDIS_URL)

    @property
    def allowed_origins(self) -> List[str]:
        return _split_csv(self.ALLOWED_ORIGINS_RAW)

    @property
    def llm_fallback_chain(self) -> List[Dict[str, Any]]:
        if not self.LLM_FALLBACK_CHAIN:
            return []
        import json
        try:
            parsed = json.loads(self.LLM_FALLBACK_CHAIN)
            if isinstance(parsed, list):
                return parsed
            return []
        except Exception as e:
            logger.warning("Failed to parse LLM_FALLBACK_CHAIN: %s", e)
            return []


settings = Settings()


# ---------------------------------------------------------------------------
# Fail-fast production validation
# ---------------------------------------------------------------------------

# Providers supported for LLM reasoning — kept consistent across llm.py,
# startup validation and the readiness probe.
SUPPORTED_LLM_PROVIDERS = {"openrouter", "openai", "groq"}
SUPPORTED_TRANSCRIBE_PROVIDERS = {"groq", "deepgram"}
SUPPORTED_EMBEDDING_PROVIDERS = {"openai", "openrouter"}


def llm_key_for(provider: str) -> Optional[str]:
    return {
        "openrouter": settings.OPENROUTER_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "groq": settings.GROQ_API_KEY,
    }.get(provider)


def transcribe_key_for(provider: str) -> Optional[str]:
    return {
        "groq": settings.GROQ_API_KEY,
        "deepgram": settings.DEEPGRAM_API_KEY,
    }.get(provider)


def embedding_key_for(provider: str) -> Optional[str]:
    return {
        "openai": settings.OPENAI_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }.get(provider)


def collect_config_problems() -> List[str]:
    """Return a list of *production-critical* configuration problems.

    Empty list means the current configuration is safe to run in production.
    Used by ``validate_required_config`` (fail-fast at startup) and surfaced as
    booleans (never values) by the readiness probe.
    """
    problems: List[str] = []

    # LLM provider + key
    provider = settings.LLM_PROVIDER
    if provider not in SUPPORTED_LLM_PROVIDERS:
        problems.append(
            f"Unsupported LLM_PROVIDER '{provider}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_LLM_PROVIDERS))}."
        )
    elif not llm_key_for(provider):
        problems.append(f"LLM_PROVIDER is '{provider}' but its API key is missing.")

    # Transcription is only required when audio processing is enabled.
    if settings.ENABLE_AUDIO:
        t_provider = settings.TRANSCRIBE_PROVIDER
        if t_provider not in SUPPORTED_TRANSCRIBE_PROVIDERS:
            problems.append(
                f"Unsupported TRANSCRIBE_PROVIDER '{t_provider}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_TRANSCRIBE_PROVIDERS))}."
            )
        elif not transcribe_key_for(t_provider):
            problems.append(
                f"ENABLE_AUDIO is set but TRANSCRIBE_PROVIDER '{t_provider}' key is missing."
            )

    # Embeddings are only required when enabled.
    if settings.ENABLE_EMBEDDINGS:
        e_provider = settings.EMBEDDING_PROVIDER
        if e_provider not in SUPPORTED_EMBEDDING_PROVIDERS:
            problems.append(
                f"Unsupported EMBEDDING_PROVIDER '{e_provider}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EMBEDDING_PROVIDERS))}."
            )
        elif not embedding_key_for(e_provider):
            problems.append(
                f"ENABLE_EMBEDDINGS is set but EMBEDDING_PROVIDER '{e_provider}' key is missing."
            )

    # Production-only hard requirements.
    if settings.is_production:
        if not settings.AI_INTERNAL_SERVICE_TOKEN:
            problems.append(
                "AI_INTERNAL_SERVICE_TOKEN is required in production to protect /internal/* endpoints."
            )
        if not settings.allowed_origins:
            problems.append(
                "ALLOWED_ORIGINS must be set explicitly in production (empty blocks all browser origins)."
            )
        if not settings.DATABASE_URL:
            problems.append(
                "DATABASE_URL is required in production for durable job/chunk/result storage."
            )
        if not settings.REDIS_URL and not getattr(settings, "ALLOW_INPROCESS_QUEUE_IN_PRODUCTION", False):
            problems.append(
                "REDIS_URL is required in production for durable queue infrastructure (or set ALLOW_INPROCESS_QUEUE_IN_PRODUCTION=true)."
            )

    return problems


def validate_required_config() -> None:
    """Fail fast in production when required configuration is missing.

    In non-production environments problems are advisory (logged as warnings by
    ``app.startup``) so local/demo work is never blocked.
    """
    problems = collect_config_problems()
    if problems and settings.is_production:
        raise RuntimeError(
            "Invalid production configuration:\n- " + "\n- ".join(problems)
        )
