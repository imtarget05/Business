"""Provider factory - the single place that maps config -> implementation.

Implements the LLM fallback policy (Phase F): when the configured provider is a
real one (Ollama / Cloudflare / external), it is wrapped in a FallbackLLMProvider
that auto-switches to the next provider on failure and always ends in Mock so
the system never hard-fails.

The embedding provider is selected independently via get_embedding_provider()
(see packages.llm.embeddings) so semantic search can use a different backend
than text generation.
"""

from __future__ import annotations

from packages.config.settings import Settings
from packages.llm.base import EmbeddingProvider, LLMProvider
from packages.llm.cloudflare import CloudflareAIProvider
from packages.llm.embeddings import (
    CloudflareEmbeddingProvider,
    MockEmbeddingProvider,
    OllamaEmbeddingProvider,
)
from packages.llm.external_openai import ExternalOpenAICompatibleProvider
from packages.llm.fallback import FallbackLLMProvider
from packages.llm.mock import MockLLMProvider
from packages.llm.ollama import OllamaProvider


def _build_real_providers(settings: Settings) -> list[LLMProvider]:
    """Build the ordered fallback list from a single configured provider kind.

    Order: configured real provider first, then a cloud alternative if creds
    are present, then Mock (added automatically by FallbackLLMProvider)."""
    kind = settings.llm_provider
    providers: list[LLMProvider] = []

    if kind.value == "ollama":
        providers.append(OllamaProvider(settings))
        # If cloud creds exist, add as backup before mock.
        if settings.cloudflare_account_id and settings.cloudflare_api_token:
            providers.append(CloudflareAIProvider(settings))
    elif kind.value == "cloudflare_ai":
        providers.append(CloudflareAIProvider(settings))
        # Local Ollama as backup if reachable config present.
        if settings.ollama_base_url:
            providers.append(OllamaProvider(settings))
    elif kind.value == "external_openai_compatible":
        providers.append(ExternalOpenAICompatibleProvider(settings))
    elif kind.value == "mock":
        return [MockLLMProvider()]
    else:
        raise ValueError(f"Unknown LLM provider: {kind!r}")
    return providers


def get_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider.value == "mock":
        return MockLLMProvider()
    real = _build_real_providers(settings)
    if not real:
        return MockLLMProvider()
    return FallbackLLMProvider(real)


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Select an EmbeddingProvider based on settings.

    ``settings.embedding_provider`` is honored FIRST (its default is ``mock``,
    so an explicit value always wins). Only when that value is an ``auto``/None
    sentinel do we fall back to the chat provider (e.g. an Ollama chat setup
    also serving embeddings). Anything unconfigured falls back to the
    deterministic, offline MockEmbeddingProvider.
    """
    ep = settings.embedding_provider
    if ep.value == "mock":
        return MockEmbeddingProvider(dim=settings.embedding_dimensions)
    if ep.value == "cloudflare_ai":
        return CloudflareEmbeddingProvider(settings)
    if ep.value == "external_openai_compatible":
        raise NotImplementedError(
            "external_openai_compatible embedding provider not implemented yet"
        )
    # Fallback path: derive from the chat (LLM) provider. EmbeddingProviderKind
    # has no ``ollama`` value, so this only triggers via the sentinel branch.
    if settings.llm_provider.value == "ollama":
        return OllamaEmbeddingProvider(settings)
    if settings.llm_provider.value == "cloudflare_ai":
        return CloudflareEmbeddingProvider(settings)
    return MockEmbeddingProvider(dim=settings.embedding_dimensions)
