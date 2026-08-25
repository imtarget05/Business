"""External OpenAI-compatible provider skeleton (e.g. Groq, Together, vLLM).

Phase 0: intentionally NOT implemented.
"""

from __future__ import annotations

from typing import Any

from packages.config.settings import Settings
from packages.llm.base import T, provider_error


class ExternalOpenAICompatibleProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model = settings.llm_model or "gpt-4o-mini"

    @property
    def name(self) -> str:
        return "external_openai_compatible"

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        # TODO(PHASE-1): standard chat/completions HTTP call using llm_api_key.
        raise provider_error(self.name, "not implemented in Phase 0")

    async def generate_structured(
        self, prompt: str, schema: type[T], **kwargs: Any
    ) -> T:
        raise provider_error(self.name, "not implemented in Phase 0")

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise provider_error(self.name, "not implemented in Phase 0")
