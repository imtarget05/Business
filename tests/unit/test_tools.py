"""Unit tests for the tool protocol + tool-call loop (Phase 3, Task 3.1)."""

from __future__ import annotations

from typing import Any

import pytest

from packages.core.errors import AgentExecutionError
from packages.core.tools import Tool, ToolRegistry, execute_tool_loop
from packages.llm.mock import MockLLMProvider


class EchoTool(Tool):
    name = "echo"
    description = "Echo back the given text."
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, arguments: dict[str, Any]) -> str:
        self.calls.append(arguments)
        return f"echo: {arguments['text']}"


class AddTool(Tool):
    name = "add"
    description = "Add two integers."
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"],
    }

    async def run(self, arguments: dict[str, Any]) -> str:
        return str(arguments["a"] + arguments["b"])


TOOL_CALL = {
    "tool_calls": [
        {
            "id": "call_1",
            "name": "echo",
            "arguments": {"text": "hello"},
        }
    ]
}


@pytest.mark.asyncio
async def test_tool_invoked_and_final_result_returned() -> None:
    tool = EchoTool()
    registry = ToolRegistry(tool)
    llm = MockLLMProvider(
        [
            TOOL_CALL,  # round 1: model asks for the echo tool
            "final answer based on echo result",  # round 2: final text
        ]
    )

    result = await execute_tool_loop(llm, "say hello via tool", registry)

    assert result == "final answer based on echo result"
    assert tool.calls == [{"text": "hello"}]
    # Round 2 must include the tool result fed back into the conversation.
    second_round = llm.calls[1]
    tool_msg = [m for m in second_round["messages"] if m["role"] == "tool"]
    assert tool_msg and tool_msg[0]["content"] == "echo: hello"


@pytest.mark.asyncio
async def test_multi_step_loop_two_tool_calls() -> None:
    echo, add = EchoTool(), AddTool()
    registry = ToolRegistry(echo, add)
    llm = MockLLMProvider(
        [
            {"tool_calls": [{"id": "c1", "name": "add", "arguments": {"a": 2, "b": 3}}]},
            {
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "echo",
                        "arguments": {"text": "5"},
                    }
                ]
            },
            "done: echo: 5",
        ]
    )

    result = await execute_tool_loop(llm, "compute", registry)

    assert result == "done: echo: 5"
    assert len(echo.calls) == 1
    assert len(llm.calls) == 3


@pytest.mark.asyncio
async def test_registry_dispatches_to_correct_tool() -> None:
    registry = ToolRegistry(EchoTool(), AddTool())
    assert set(registry.names()) == {"add", "echo"}
    assert registry.get("echo").name == "echo"
    specs = registry.list_schemas()
    assert all({"name", "description", "parameters"} <= set(s) for s in specs)

    with pytest.raises(AgentExecutionError):
        registry.get("nope")


@pytest.mark.asyncio
async def test_duplicate_registration_rejected() -> None:
    registry = ToolRegistry(EchoTool())
    with pytest.raises(AgentExecutionError):
        registry.register(EchoTool())


@pytest.mark.asyncio
async def test_unknown_tool_in_call_raises() -> None:
    registry = ToolRegistry(EchoTool())
    llm = MockLLMProvider([{"tool_calls": [{"id": "x", "name": "ghost", "arguments": {}}]}])
    with pytest.raises(AgentExecutionError):
        await execute_tool_loop(llm, "p", registry)


@pytest.mark.asyncio
async def test_non_converging_loop_raises_after_max_rounds() -> None:
    registry = ToolRegistry(EchoTool())
    llm = MockLLMProvider([TOOL_CALL] * 10)
    with pytest.raises(AgentExecutionError):
        await execute_tool_loop(llm, "p", registry, max_rounds=3)


@pytest.mark.asyncio
async def test_no_tools_scripted_returns_content_directly() -> None:
    registry = ToolRegistry(EchoTool())
    llm = MockLLMProvider(["just answer"])
    assert await execute_tool_loop(llm, "p", registry) == "just answer"
