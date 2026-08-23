"""Application settings.

All configuration is environment-driven (12-factor). Secrets are NEVER
hard-coded; see `.env.example` at the repository root.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProviderKind(StrEnum):
    """Which LLM provider implementation the abstraction should activate.

    `mock` requires no network access and no credentials: the application must
    always be runnable with it (ADR-001 / ADR-005). Ollama is an OPTIONAL
    provider only — the system never depends on a local model.
    """

    MOCK = "mock"
    CLOUDFLARE_AI = "cloudflare_ai"
    EXTERNAL_OPENAI_COMPATIBLE = "external_openai_compatible"
    OLLAMA = "ollama"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---------------------------------------------------------------
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"

    # --- Database (Neon PostgreSQL in production; pgvector-enabled locally) ---
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/business_ops"
    )
    db_echo: bool = False

    # --- LLM ------------------------------------------------------------------
    llm_provider: LLMProviderKind = LLMProviderKind.MOCK
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_request_timeout_seconds: float = 30.0

    # --- Cloudflare Workers AI (optional provider) -----------------------------
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None

    # --- Ollama (optional provider only) ---------------------------------------
    ollama_base_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()  # type: ignore[call-arg]




