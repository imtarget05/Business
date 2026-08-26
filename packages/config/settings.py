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


class EmbeddingProviderKind(StrEnum):
    """Which embedding provider implementation the abstraction should activate.

    `mock` requires no network access and no credentials: the application must
    always be runnable with it (ADR-001 / ADR-005). Cloudflare is the primary
    production provider; external OpenAI-compatible is supported for flexibility.
    """

    MOCK = "mock"
    CLOUDFLARE_AI = "cloudflare_ai"
    EXTERNAL_OPENAI_COMPATIBLE = "external_openai_compatible"


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
    # API key for the minimum authn boundary (X-API-Key header). Empty = open.
    api_key: str | None = None
    # Per-tenant keys: X-API-Key value -> organization_id (UUID string).
    # When non-empty, callers are bound to their organization server-side;
    # client-supplied organization_id values are ignored everywhere.
    tenant_api_keys: dict[str, str] = {}

    # --- Database (Neon PostgreSQL in production; pgvector-enabled locally) ---
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/business_ops"
    )
    db_echo: bool = False
    # When true, POST /v1/tasks persists task progress and final results to the
    # database and enables idempotent replay. Defaults to false so the app and
    # CI tests run with zero external dependencies; enable in Docker/production.
    persistence_enabled: bool = False

    # --- LLM ----------------------------------------------------------------
    llm_provider: LLMProviderKind = LLMProviderKind.MOCK
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_request_timeout_seconds: float = 30.0
    # Retry policy for HTTP-based providers (Cloudflare / OpenAI-compatible).
    llm_max_retries: int = 2
    llm_retry_backoff_seconds: float = 0.25

    # --- Embedding ----------------------------------------------------------
    embedding_provider: EmbeddingProviderKind = EmbeddingProviderKind.MOCK
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int = 768  # Cloudflare @cf/baai/bge-base-en-v1.5
    embedding_request_timeout_seconds: float = 30.0
    embedding_max_retries: int = 2
    embedding_retry_backoff_seconds: float = 0.25

    # --- Knowledge retrieval (Phase 2) ---------------------------------------
    knowledge_similarity_threshold: float = 0.75
    knowledge_top_k: int = 4

    # --- Router (Phase 4) -----------------------------------------------------
    router_confidence_threshold: float = 0.6

    # --- Agent tool loop (Phase 3) -------------------------------------------
    agent_max_tool_rounds: int = 5
    # Max handoff depth for multi-agent chains (Phase 4 Task 4.2)
    agent_max_handoffs: int = 2
    # Per-task execution timeout (Phase 4 Task 4.3)
    agent_task_timeout_seconds: int = 30
    # Per-hop timeout for handoff chains (Phase 5 FIX 1)
    # Defaults to agent_task_timeout_seconds; total chain capped at 2x agent_task_timeout_seconds
    agent_hop_timeout_seconds: int = 30

    # --- Cloudflare Workers AI (optional provider) -----------------------------
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None

    # --- Ollama (optional provider only) ---------------------------------------
    ollama_base_url: str | None = None

    # --- Email (support agent) -------------------------------------------------
    # SMTP settings for send_email_reply tool. DRY-RUN mode is DEFAULT (draft-only;
    # real send behind email_send_enabled flag). YAGNI: no retries/queueing.
    email_smtp_host: str | None = None
    email_smtp_port: int = 587
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_from_address: str | None = None
    email_send_enabled: bool = False  # DRY-RUN default: False = draft-only
    # Explicit recipient allowlist for send_email_reply when sending is enabled.
    # Recipients must also match the conversation's customer record otherwise.
    email_recipient_allowlist: list[str] = []

    # --- Gmail API (support agent) ---------------------------------------------
    # Gmail API settings for send_gmail_reply tool. DRY-RUN mode is DEFAULT (draft-only;
    # real send behind gmail_send_enabled flag). Uses OAuth2 refresh token flow.
    google_refresh_token: str | None = None
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_sheet_id: str | None = None
    gmail_send_enabled: bool = False  # DRY-RUN default: False = draft-only
    # Explicit recipient allowlist for send_gmail_reply when sending is enabled.
    # Recipients must also match the conversation's customer record otherwise.
    gmail_allowed_recipients: list[str] = []


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()  # type: ignore[call-arg]