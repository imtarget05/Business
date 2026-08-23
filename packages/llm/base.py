"""LLMProvider protocol — the ONLY way business logic talks to LLMs.

Contract:
    Application -> LLMProvider -> selected provider implementation

No business code may call provider SDKs directly (ADR-005).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel

T = TypeVar("T", bound="BaseModel")


def provider_error(provider_name: str, detail: str):
    from packages.core.errors import LLMProviderError

    return LLMProviderError(f"[{provider_name}] {detail}")


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        """Return free-form completion text."""
        ...

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
        """Return a validated instance of `schema`."""
        ...
