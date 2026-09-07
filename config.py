"""
VoiceOps_Engine — Configuration Module
Loads and validates all environment variables using Pydantic Settings.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, field_validator


class Settings(BaseSettings):
    """
    Central settings object — reads from environment / .env file.
    All fields are validated at startup; missing required vars raise
    a clear ValidationError rather than cryptic runtime failures.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # ── VAPI ───────────────────────────────────────────────────────────────────
    # Defaults are safe placeholders so the app boots without real secrets.
    # Routes that need these values will fail gracefully at request time.
    vapi_api_key: str = ""
    vapi_assistant_id: str = ""
    vapi_phone_number_id: str = ""

    # ── Google Gemini ──────────────────────────────────────────────────────────
    gemini_api_key: str = ""

    # ── Groq ──────────────────────────────────────────────────────────────────
    groq_api_key: str = ""

    # ── n8n Webhooks ───────────────────────────────────────────────────────────
    n8n_midcall_webhook_url: str = ""
    n8n_postcall_webhook_url: str = ""

    # ── Validators ─────────────────────────────────────────────────────────────
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        lower = v.lower()
        if lower not in allowed:
            raise ValueError(f"app_env must be one of {allowed}")
        return lower


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    Use FastAPI's Depends(get_settings) to inject settings into routes.
    """
    return Settings()
