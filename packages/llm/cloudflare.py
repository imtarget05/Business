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

⚠️ UNVERIFIED-AGAINST-REAL-API: the embed() payload key ("text") and response
shape ("result.data") for @cf/baai/bge-base-en-v1.5 are ASSUMED based on common
embedding API patterns — they have NOT been confirmed against live Cloudflare
API documentation. Tests mock this shape; a real integration smoke-test is
required before trusting embed() in production. Same caveat applies to the
768-dim dimension claim (bge-base-en-v1.5 is documented as 768-dim on
HuggingFace, but Cloudflare's wrapper may differ).
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

    async def _run(
        self,
        payload: dict[str, Any],
        *,
        model_path: str,
        timeout_setting: str = "llm_request_timeout_seconds",
        max_retries: int | None = None,
        backoff_seconds: float | None = None,
    ) -> dict[str, Any]:
        """POST to Workers AI with retry/backoff; returns the ``result`` object.

        ``model_path`` selects which endpoint under the account base URL to hit
        (chat models and embedding models have different paths). Shared by
        both ``generate()`` (LLM) and ``embed()`` so retry/backoff behaviour
        never drifts between the two.
        """
        retries = self._max_retries if max_retries is None else max(0, max_retries)
        backoff = self._backoff if backoff_seconds is None else backoff_seconds
        last_detail = "unknown error"
        timeout_s = getattr(self._settings, timeout_setting)
        for attempt in range(retries + 1):
            try:
                resp = await self._client.post(model_path, json=payload)
            except httpx.TimeoutException:
                last_detail = f"request timed out after {timeout_s}s"
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
            if attempt < retries:
                await asyncio.sleep(backoff * (2**attempt))
        raise provider_error(
            self.name,
            f"failed after {retries + 1} attempts ({last_detail})",
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
            },
            model_path=self.model,
        )
        # Chat-style models (OpenAI-compatible) return choices[0].message.content;
        # legacy text models return a plain "response" string. Support both.
        text = result.get("response")
        if not isinstance(text, str):
            choices = result.get("choices")
            if (
                isinstance(choices, list)
                and choices
                and isinstance(choices[0], dict)
            ):
                message = choices[0].get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    text = message["content"]
        if not isinstance(text, str):
            raise provider_error(self.name, f"unexpected result shape: {result!r}")
        return text

    # ------------------------------------------------------------------
    # EmbeddingProvider protocol (Phase 2)
    # ------------------------------------------------------------------

    EMBEDDING_MODEL = "@cf/baai/bge-base-en-v1.5"  # 768-dim output

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Cloudflare bge-base-en-v1.5 response shape differs from chat:
            {
              "result": {"data": [[...768 floats], ...], "shape": [n, 768]},
              "success": true,
              ...
            }
        The vectors live under ``result.data``, NOT ``result.response``.
        """
        result = await self._run(
            {"text": texts},
            model_path=self.EMBEDDING_MODEL,
            timeout_setting="embedding_request_timeout_seconds",
            max_retries=self._settings.embedding_max_retries,
            backoff_seconds=self._settings.embedding_retry_backoff_seconds,
        )
        data = result.get("data")
        if not isinstance(data, list) or not data:
            raise provider_error(
                self.name, f"unexpected embedding result shape: {result!r}"
            )
        vectors: list[list[float]] = []
        for vec in data:
            if not isinstance(vec, list):
                raise provider_error(
                    self.name, f"embedding row is not a list: {type(vec).__name__}"
                )
            vectors.append([float(v) for v in vec])
        return vectors

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
        # Coerce loose scalar types the model guessed (int where str expected, etc.)
        try:
            return schema.model_validate(data)
        except Exception:
            pass
        try:
            coerced = {
                k: (str(v) if isinstance(v, (int, float, bool)) else v)
                for k, v in data.items()
            } if isinstance(data, dict) else data
            return schema.model_validate(coerced)
        except Exception as exc:  # pydantic validation failure
            raise provider_error(
                self.name,
                f"response does not match schema {schema.__name__}: {exc}",
            ) from exc

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """One round of chat completion with tool specs available.

        ⚠️ UNVERIFIED-AGAINST-REAL-API: assumes the Workers AI chat endpoint
        accepts OpenAI-style ``tools`` / ``tool_calls`` fields and echoes them
        in ``result.response.tool_calls`` with ``{"id", "name",
        "arguments"(object)}``. Tests mock this shape; verify against the live
        API before relying on tool calling in production.
        """
        payload_messages = self._messages("", system)[:-1] + list(messages)
        result = await self._run(
            {
                "messages": payload_messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": temperature,
                "max_tokens": max_tokens,
                **kwargs,
            },
            model_path=self.model,
        )
        response = result.get("response")
        if not isinstance(response, dict):
            # OpenAI-compatible chat models return choices[0].message;
            # legacy text models return a plain "response" string.
            choices = result.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    response = message
        if isinstance(response, str):
            return {"content": response, "tool_calls": None}
        if not isinstance(response, dict):
            raise provider_error(self.name, f"unexpected result shape: {result!r}")
        raw_calls = response.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []
        for call in raw_calls:
            fn = call.get("function") or call  # tolerate flat or nested shapes
            name = fn.get("name")
            arguments = fn.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise provider_error(
                        self.name,
                        f"tool_call {name!r} arguments are not valid JSON",
                    ) from exc
            tool_calls.append(
                {"id": call.get("id"), "name": name, "arguments": arguments}
            )
        content = response.get("content") or response.get("text")
        return {"content": content, "tool_calls": tool_calls or None}

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["CloudflareAIProvider"]
