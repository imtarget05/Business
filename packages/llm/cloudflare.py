"""Cloudflare Workers AI provider skeleton.

Phase 0: intentionally NOT implemented. Requires CLOUDFLARE_ACCOUNT_ID /
CLOUDFLARE_API_TOKEN. No Cloudflare-specific code may leak into core domain
logic (ADR-005) — this adapter is the only place these APIs appear.
"""

from __future__ import annotations

from typing import Any

from packages.config.settings import Settings
from packages.llm.base import T, provider_error


class CloudflareAIProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model = settings.llm_model or "@cf/meta/llama-3.1-8b-instruct"

    @property
    def name(self) -> str:
        return "cloudflare_ai"

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        # TODO(PHASE-1): POST https://api.cloudflare.com/client/v4/accounts/
        #                 {account_id}/ai/run/{model}
        raise provider_error(
            self.name,
            "not implemented in Phase 0 — configure credentials and implement "
            "in Phase 1 (see docs/adr/ADR-005-llm-provider-abstraction.md)",
        )

    async def generate_structured(
        self, prompt: str, schema: type[T], **kwargs: Any
    ) -> T:
        raise provider_error(self.name, "not implemented in Phase 0")
