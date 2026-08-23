"""Cloudflare Workers AI provider.

Phase 1 implementation of the ``LLMProvider`` protocol (ADR-005). This adapter
is the ONLY place where Cloudflare-specific APIs appear — no Cloudflare code
may leak into core domain logic. Credentials come from Settings (env / .env),
never hard-coded.

Endpoint:
    POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}

Retry policy: transient failures (connection errors, timeouts, HTTP 408/429/5xx)
are retried with exponential backoff (``llm_retry_backoff_seconds * 2**attempt``,
up to ``llm_max_retries`` retries). Non-transient errors (other 4xx) and
exhausted retries raise :class:`~packages.core.errors.LLMProviderError`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from packages.config.settings import Settings
from packages.llm.base import T, provider_error

# HTTP statuses considered transient and therefore retryable.
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class CloudflareAIProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
            raise provider_error(
                self.name,
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be set "
                "when LLM_PROVIDER=cloudflare_ai",
            )
        self._settings = settings
        self.model = settings.llm_model or "@cf/meta/llama-3.1-8b-instruct"
        self._max_retries = max(0, settings.llm_max_retries)
        self._backoff = settings.llm_retry_backoff_seconds
        # Transport is injectable so tests can use httpx.MockTransport without
        # any real network access (no credentials are ever needed in CI).
        self._client = httpx.AsyncClient(
            base_url=(
                "https://api.cloudflare.com/client/v4/accounts/"
                f"{settings.cloudflare_account_id}/ai/run/"
            ),
            headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
            timeout=settings.llm_request_timeout_seconds,
            transport=transport,
        )

    @property
    def name(self) -> str:
        return "cloudflare_ai"

    async def _run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to Workers AI with retry/backoff; returns the ``result`` object."""
        last_detail = "unknown error"
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(self.model, json=payload)
            except httpx.TimeoutException:
                last_detail = (
                    f"request timed out after "
                    f"{self._settings.llm_request_timeout_seconds}s"
                )
            except httpx.HTTPError as exc:  # connection & protocol errors
                last_detail = f"HTTP error: {type(exc).__name__}: {exc}"
            else:
                if resp.status_code in _RETRYABLE_STATUSES:
                    last_detail = f"transient HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    raise provider_error(
                        self.name, f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                else:
                    body = resp.json()
                    if not body.get("success"):
                        errors = "; ".join(str(e) for e in body.get("errors", []))
                        raise provider_error(
                            self.name, f"API reported failure: {errors or body!r}"
                        )
                    return body.get("result") or {}
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise provider_error(
            self.name,
            f"failed after {self._max_retries + 1} attempts ({last_detail})",
        )

    @staticmethod
    def _messages(prompt: str, system: str | None) -> list[dict[str, str]]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        result = await self._run(
            {
                "messages": self._messages(prompt, system),
                "temperature": temperature,
                "max_tokens": max_tokens,
                **kwargs,
            }
        )
        text = result.get("response")
        if not isinstance(text, str):
            raise provider_error(self.name, f"unexpected result shape: {result!r}")
        return text

    async def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> T:
        raw = await self.generate(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise provider_error(
                self.name, f"response is not valid JSON: {raw[:200]!r}"
            ) from exc
        try:
            return schema.model_validate(data)
        except Exception as exc:  # pydantic validation failure
            raise provider_error(
                self.name,
                f"response does not match schema {schema.__name__}: {exc}",
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["CloudflareAIProvider"]
