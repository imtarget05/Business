"""MockLLMProvider — the only fully functional provider in Phase 0.

Deterministic and credential-free so tests and local dev never need network
access or a local model.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from packages.llm.base import T

DEFAULT_TEXT = (
    "[mock-llm] This is a deterministic placeholder response from "
    "MockLLMProvider. No model was executed."
)


class MockLLMProvider:
    """Scriptable in-memory provider.

    Optionally feed scripted outputs via constructor or `script()`; each call
    consumes the next script entry. Unscripted calls return deterministic
    defaults.
    """

    def __init__(self, scripted: list[str | dict[str, Any]] | None = None) -> None:
        self._script: deque[str | dict[str, Any]] = deque(scripted or [])
        self.calls: list[dict[str, Any]] = []
        self.tool_responses: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "mock"

    def script(self, *outputs: str | dict[str, Any]) -> None:
        self._script.extend(outputs)

    def _next_raw(self) -> str | dict[str, Any]:
        if self._script:
            return self._script.popleft()
        return DEFAULT_TEXT

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        raw = self._next_raw()
        if isinstance(raw, dict):
            return json.dumps(raw)
        return raw

    async def generate_structured(
        self,
        prompt: str,
        schema: T | dict[str, Any],
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> T:
        schema_name = getattr(schema, "__name__", "dict")
        self.calls.append({"prompt": prompt, "schema": schema_name})
        raw = self._next_raw()
        if isinstance(raw, dict):
            # schema could be a dict (PO Agent passes dict) or a Pydantic class
            if hasattr(schema, "model_validate"):
                return schema.model_validate(raw)
            return raw  # return dict as-is when schema is dict
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "MockLLMProvider received unscripted structured call; script a "
                "dict output matching the requested schema."
            ) from exc
        if hasattr(schema, "model_validate"):
            return schema.model_validate(data)
        return data

    # ------------------------------------------------------------------
    # Tool-call protocol (Phase 3, Task 3.1) — fully scripted, no network.
    # ------------------------------------------------------------------

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
        """Consume the next scripted entry.

        Script formats:
          - ``{"tool_calls": [{"id", "name", "arguments"}], "content": ...}``
            -> returned as-is (the loop dispatches the calls).
          - plain str / other dict -> final answer ``{"content": ...}``.
        """
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        raw = self._next_raw()
        if isinstance(raw, dict) and "tool_calls" in raw:
            response = {
                "content": raw.get("content"),
                "tool_calls": raw["tool_calls"],
            }
        else:
            content = json.dumps(raw) if isinstance(raw, dict) else str(raw)
            response = {"content": content, "tool_calls": None}
        self.tool_responses.append(response)
        return response
