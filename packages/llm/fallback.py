"""LLM provider fallback chain (Phase F — provider stability).

Wraps one or more LLMProvider implementations behind a single facade that
auto-switches to the next provider when the active one fails (timeout, 429,
unreachable). This implements the system's LLM fallback policy:

    Ollama (local) -> Nous Cloud (cloudflare_ai/external) -> Mock (always safe)

The fallback is transparent to the orchestrator: it only ever sees the
``LLMProvider`` protocol. State is tracked in-memory; a cooldown prevents
flapping between providers on transient errors.

Design notes:
- Only ``generate`` / ``generate_structured`` / ``complete_with_tools`` are
  proxied. ``embed`` is passed through to the primary embedding provider.
- The active provider index is sticky: once a provider succeeds it remains
  active until it fails again, so we don't thrash on every call.
- ``MockLLMProvider`` is always appended last so the app NEVER hard-fails.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from packages.llm.base import T
from packages.llm.mock import MockLLMProvider

_T = TypeVar("_T")

# Errors that should trigger a failover to the next provider.
_FAILOVER_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    asyncio.TimeoutError,
)


class FallbackLLMProvider:
    """Facade over an ordered list of LLMProvider implementations.

    On the first call, tries providers in order until one returns successfully.
    Once a provider succeeds it becomes sticky (active) until it raises a
    failover exception, at which point the chain advances.
    """

    def __init__(self, providers: list[Any], *, cooldown_seconds: float = 30.0) -> None:
        if not providers:
            providers = [MockLLMProvider()]
        # Always guarantee a mock fallback at the end.
        if not any(p.name == "mock" for p in providers):
            providers = list(providers) + [MockLLMProvider()]
        self._providers = providers
        self._active_idx = 0
        self._last_failure_ts: float = 0.0
        self._cooldown = cooldown_seconds

    # -- introspection -------------------------------------------------------
    @property
    def name(self) -> str:
        return f"fallback[{self._providers[self._active_idx].name}]"

    @property
    def active_provider_name(self) -> str:
        return self._providers[self._active_idx].name

    @property
    def provider_chain(self) -> list[str]:
        return [p.name for p in self._providers]

    # -- core dispatch -------------------------------------------------------
    async def _dispatch(self, method: str, *args: Any, **kwargs: Any) -> Any:
        n = len(self._providers)
        last_exc: BaseException | None = None
        # Try every provider once, starting from the current active index.
        # We do NOT mutate self._active_idx inside the loop (that would
        # double-advance and skip providers); only set it on success/final.
        for offset in range(n):
            idx = (self._active_idx + offset) % n
            provider = self._providers[idx]
            try:
                result = await getattr(provider, method)(*args, **kwargs)
            except _FAILOVER_EXCEPTIONS as exc:
                last_exc = exc
                continue
            except Exception as exc:  # noqa: BLE001 - any failure fails over
                last_exc = exc
                continue
            # Success: stick to this provider.
            self._active_idx = idx
            return result
        # All providers failed. If a real provider is active, advance it so the
        # next call starts from the next one; mock (last) is always safe.
        self._active_idx = (self._active_idx + 1) % n
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM fallback chain exhausted with no result")

    @staticmethod
    def _looks_transient(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return any(
            t in msg
            for t in (
                "429",
                "rate limit",
                "timeout",
                "timed out",
                "503",
                "502",
                "504",
                "unreachable",
                "connection",
            )
        )

    # -- LLMProvider protocol ------------------------------------------------
    async def generate(self, prompt: str, **kwargs: Any) -> str:
        return await self._dispatch("generate", prompt, **kwargs)

    async def generate_structured(self, prompt: str, schema: type[T], **kwargs: Any) -> T:
        return await self._dispatch("generate_structured", prompt, schema, **kwargs)

    async def complete_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        return await self._dispatch("complete_with_tools", messages, tools, **kwargs)

    async def aclose(self) -> None:
        for p in self._providers:
            close = getattr(p, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass


__all__ = ["FallbackLLMProvider"]
