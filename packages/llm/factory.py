"""Provider factory — the single place that maps config -> implementation."""

from __future__ import annotations

from packages.config.settings import Settings
from packages.llm.base import EmbeddingProvider, LLMProvider
from packages.llm.cloudflare import CloudflareAIProvider
from packages.llm.external_openai import ExternalOpenAICompatibleProvider
from packages.llm.mock import MockLLMProvider
from packages.llm.mock_embedding import MockEmbeddingProvider
from packages.llm.ollama import OllamaProvider


def get_llm_provider(settings: Settings) -> LLMProvider:
    kind = settings.llm_provider
    if kind.value == "mock":
        return MockLLMProvider()
    if kind.value == "cloudflare_ai":
        return CloudflareAIProvider(settings)
    if kind.value == "external_openai_compatible":
        return ExternalOpenAICompatibleProvider(settings)
    if kind.value == "ollama":
        return OllamaProvider(settings)
    raise ValueError(f"Unknown LLM provider: {kind!r}")


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Select an EmbeddingProvider based on ``settings.embedding_provider``.

    ``cloudflare_ai`` returns a CloudflareAIProvider — the same class also
    implements the EmbeddingProvider protocol via its ``embed()`` method
    (duck-typed; no separate class needed).
    """
    kind = settings.embedding_provider
    if kind.value == "mock":
        return MockEmbeddingProvider(dim=settings.embedding_dimensions)
    if kind.value == "cloudflare_ai":
        return CloudflareAIProvider(settings)
    if kind.value == "external_openai_compatible":
        raise NotImplementedError(
            "external_openai_compatible embedding provider not implemented yet"
        )
    raise ValueError(f"Unknown embedding provider: {kind!r}")
