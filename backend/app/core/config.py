"""
PSX Sentinel — Application Configuration

All configuration is loaded from environment variables using pydantic-settings.
The get_settings() function uses @lru_cache to ensure only one Settings instance
is ever created (singleton pattern). This prevents repeated .env file reads and
environment variable parsing on every request.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Central configuration for PSX Sentinel.

    Every field maps to an environment variable of the same name (case-insensitive).
    Required fields (no default) will cause a startup error if missing, ensuring
    the application never runs in an insecure or misconfigured state.
    """

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_URL: str  # Required — e.g. "postgresql+asyncpg://user:pass@host/db"

    # ── Redis ──────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Authentication ─────────────────────────────────────────────────────────
    SECRET_KEY: str  # Required — generate with: openssl rand -hex 32
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── LLM Provider Keys ─────────────────────────────────────────────────────
    GROQ_API_KEY: str  # Required — primary LLM provider
    GEMINI_API_KEY: str = ""  # Optional — fallback LLM provider
    CAPITALSTAKE_API_KEY: str = ""  # Optional — financial data API

    # ── Application ────────────────────────────────────────────────────────────
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    NIGHTLY_PIPELINE_HOUR: int = 20  # 8 PM PKT — run daily data pipeline

    # ── PSX Tickers ────────────────────────────────────────────────────────────
    PSX_TICKERS: str = "ENGRO,LUCK,OGDC,PPL,MCB,HBL,UBL,MARI,PSO,MEBL"

    # ── CORS / Frontend ───────────────────────────────────────────────────────
    FRONTEND_URL: str = "http://localhost:3000"

    # ── Celery ─────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    @property
    def tickers_list(self) -> list[str]:
        """Split comma-separated ticker string into a clean list."""
        return [t.strip().upper() for t in self.PSX_TICKERS.split(",") if t.strip()]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache()
def get_settings() -> Settings:
    """
    Return the cached singleton Settings instance.

    Using @lru_cache ensures the .env file is read only once and the same
    Settings object is reused across the entire application lifecycle.
    """
    return Settings()
