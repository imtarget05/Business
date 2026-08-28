"""Application settings.

All configuration is environment-driven (12-factor). Secrets are NEVER
hard-coded; see `.env.example` at the repository root.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProviderKind(StrEnum):
    """Which LLM provider implementation the abstraction should activate."""

    MOCK = "mock"
    CLOUDFLARE_AI = "cloudflare_ai"
    EXTERNAL_OPENAI_COMPATIBLE = "external_openai_compatible"
    OLLAMA = "ollama"


class EmbeddingProviderKind(StrEnum):
    """Which embedding provider implementation the abstraction should activate."""

    MOCK = "mock"
    CLOUDFLARE_AI = "cloudflare_ai"
    EXTERNAL_OPENAI_COMPATIBLE = "external_openai_compatible"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # --- Core ---------------------------------------------------------------
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"
    # API key for the minimum authn boundary (X-API-Key header). Empty = open.
    api_key: str | None = None
    # Per-tenant keys: X-API-Key value -> organization_id (UUID string).
    tenant_api_keys: dict[str, str] = {}
    # Rate limiting: requests per minute per API key (sliding window).
    rate_limit_per_minute: int = 60

    # --- Database (Neon PostgreSQL in production; pgvector-enabled locally) ---
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/business_ops"
    )
    db_echo: bool = False
    persistence_enabled: bool = False

    # --- LLM ----------------------------------------------------------------
    llm_provider: LLMProviderKind = LLMProviderKind.MOCK
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_request_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_retry_backoff_seconds: float = 0.25

    # --- Embedding ----------------------------------------------------------
    embedding_provider: EmbeddingProviderKind = EmbeddingProviderKind.MOCK
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int = 768
    embedding_request_timeout_seconds: float = 30.0
    embedding_max_retries: int = 2
    embedding_retry_backoff_seconds: float = 0.25

    # --- Knowledge retrieval -------------------------------------------------
    knowledge_similarity_threshold: float = 0.75
    knowledge_top_k: int = 4

    # --- Router --------------------------------------------------------------
    router_confidence_threshold: float = 0.6

    # --- Agent tool loop -----------------------------------------------------
    agent_max_tool_rounds: int = 5
    agent_max_handoffs: int = 2
    agent_task_timeout_seconds: int = 30
    agent_hop_timeout_seconds: int = 30

    # --- LangGraph -----------------------------------------------------------
    langgraph_enabled: bool = False
    langgraph_checkpointer_db: str = "checkpoints.sqlite"

    # --- Supply Chain ---------------------------------------------------------
    po_approval_thresholds: dict[str, float] = {"manager_a": 500.0, "manager_b": 5000.0}

    # --- Cloudflare Workers AI -----------------------------------------------
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None

    # --- Ollama --------------------------------------------------------------
    ollama_base_url: str | None = None

    # --- Reporting -----------------------------------------------------------
    reporting_sheet_log_enabled: bool = False

    # --- Email ----------------------------------------------------------------
    email_smtp_host: str | None = None
    email_smtp_port: int = 587
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_from_address: str | None = None
    email_send_enabled: bool = False
    email_recipient_allowlist: list[str] = []

    # --- Gmail API -----------------------------------------------------------
    google_refresh_token: str | None = None
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_sheet_id: str | None = None
    gmail_send_enabled: bool = False
    gmail_allowed_recipients: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _sanitize_empty_env(cls, data: dict) -> dict:
        """Convert empty string env vars to appropriate empty containers.

        Pydantic_settings json-loads string env vars; empty strings for
        dict/list fields raise JSONDecodeError. This handles that gracefully.
        """
        if not isinstance(data, dict):
            return data

        # Dict fields
        for key in ("tenant_api_keys", "po_approval_thresholds"):
            val = data.get(key)
            if val == "":
                data[key] = {}

        # List fields
        for key in ("email_recipient_allowlist", "gmail_allowed_recipients"):
            val = data.get(key)
            if val == "":
                data[key] = []

        return data


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()  # type: ignore[call-arg]