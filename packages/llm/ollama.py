"""Ollama provider skeleton.

OPTIONAL ONLY (ADR-001): the application must run without Ollama and without
any local model. This provider is never activated unless explicitly selected
via `LLM_PROVIDER=ollama`.
"""

from __future__ import annotations

from typing import Any

from packages.config.settings import Settings
from packages.llm.base import T, provider_error


class OllamaProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.ollama_base_url
        self.model = settings.llm_model or "llama3.1"

    @property
    def name(self) -> str:
        return "ollama"

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        # TODO(PHASE-1+): POST {base_url}/api/generate
        raise provider_error(
            self.name,
            "not implemented in Phase 0 — Ollama is an optional provider and "
            "is NOT required to run the platform",
        )

    async def generate_structured(
        self, prompt: str, schema: type[T], **kwargs: Any
    ) -> T:
        raise provider_error(self.name, "not implemented in Phase 0")
