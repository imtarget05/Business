"""Tool protocol + per-agent tool registry (Phase 3, Task 3.1).

A Tool is a named, schema-described callable an agent can invoke. The
tool-call loop lives here (:func:`execute_tool_loop`) and is deliberately
sequential (YAGNI per controller ruling): one tool call at a time, no async
scheduling, no parallel dispatch beyond what the loop needs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from packages.core.errors import AgentExecutionError

# Maximum consecutive tool-call rounds before giving up (guards runaway loops).
DEFAULT_MAX_TOOL_ROUNDS = 5


class Tool(ABC):
    """Base class for agent tools.

    Subclasses declare:
      - ``name``: unique identifier the LLM uses in tool_calls.
      - ``description``: short human/LLM-readable summary.
      - ``schema``: JSON-Schema dict describing the ``arguments`` object.
    """

    name: str
    description: str = ""
    schema: dict[str, Any]

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> str:
        """Execute the tool and return a string result for the LLM."""
        ...


@runtime_checkable
class ToolLike(Protocol):
    """Structural alternative to :class:`Tool` for duck-typed tools."""

    name: str
    description: str
    schema: dict[str, Any]

    async def run(self, arguments: dict[str, Any]) -> str: ...


class ToolRegistry:
    """Per-agent registry mapping tool names to tool instances."""

    def __init__(self, *tools: ToolLike) -> None:
        self._tools: dict[str, ToolLike] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolLike) -> None:
        if tool.name in self._tools:
            raise AgentExecutionError(f"tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolLike:
        try:
            return self._tools[name]
        except KeyError:
            raise AgentExecutionError(f"unknown tool: {name!r}") from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def list_schemas(self) -> list[dict[str, Any]]:
        """Provider-agnostic tool specs handed to ``complete_with_tools``."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.schema,
            }
            for t in self._tools.values()
        ]


async def execute_tool_loop(
    provider: Any,
    prompt: str,
    registry: ToolRegistry,
    *,
    system: str | None = None,
    max_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    on_tool_call: Any | None = None,
) -> str:
    """Run the tool-call loop until the model returns a final text answer.

    Each round: ask the provider for a completion with the registered tool
    specs. If it returns tool_calls, dispatch each sequentially through the
    registry, feed results back, and continue. Otherwise return the text.

    Args:
        on_tool_call: Optional callback receiving each executed tool call as
            (name: str, arguments: dict, result: str, mode: str | None).
            Allows callers to capture action metadata without duplicating the loop.
    """
    conversation: list[dict[str, Any]] = [
        {"role": "user", "content": prompt}
    ]
    for _ in range(max(1, max_rounds)):
        response = await provider.complete_with_tools(
            conversation,
            registry.list_schemas(),
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            content = response.get("content")
            if not isinstance(content, str):
                raise AgentExecutionError(
                    "provider returned neither tool_calls nor text content"
                )
            return content
        conversation.append(
            {
                "role": "assistant",
                "content": response.get("content"),
                "tool_calls": tool_calls,
            }
        )
        for call in tool_calls:
            name = call.get("name")
            if not isinstance(name, str):
                continue
            arguments = call.get("arguments") or {}
            tool = registry.get(name)
            result = await tool.run(arguments)

            # Invoke callback if provided
            if on_tool_call is not None:
                mode = None
                # Extract DRY_RUN mode from send_email_reply results
                if name == "send_email_reply":
                    import json

                    try:
                        result_data = json.loads(result)
                        mode = result_data.get("mode")
                    except Exception:
                        pass
                await on_tool_call(name, arguments, result, mode)

            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": result,
                }
            )
    raise AgentExecutionError(
        f"tool-call loop did not converge after {max_rounds} rounds"
    )


async def _dispatch(registry: ToolRegistry, call: dict[str, Any]) -> str:
    name = call.get("name")
    if not isinstance(name, str):
        raise AgentExecutionError(f"tool_call missing name: {call!r}")
    arguments = call.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise AgentExecutionError(
            f"tool_call {name!r} arguments must be a dict, got "
            f"{type(arguments).__name__}"
        )
    tool = registry.get(name)
    return await tool.run(arguments)


__all__ = [
    "Tool",
    "ToolLike",
    "ToolRegistry",
    "execute_tool_loop",
    "DEFAULT_MAX_TOOL_ROUNDS",
]
