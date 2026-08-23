"""Provider factory — the single place that maps config -> implementation."""

from __future__ import annotations

from packages.config.settings import Settings
from packages.llm.base import LLMProvider
from packages.llm.cloudflare import CloudflareAIProvider
from packages.llm.external_openai import ExternalOpenAICompatibleProvider
from packages.llm.mock import MockLLMProvider
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
