"""Unit tests for OllamaProvider chat functionality (TEST-001)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel

from packages.config.settings import Settings
from packages.llm.ollama import OllamaProvider


class TestModel(BaseModel):
    answer: str


@pytest.fixture
def settings() -> Settings:
    return Settings(ollama_base_url="http://localhost:11434", llm_model="qwen2.5:7b")


@pytest.fixture
def provider(settings: Settings) -> OllamaProvider:
    return OllamaProvider(settings)


@pytest.mark.asyncio
async def test_ollama_generate_success(provider: OllamaProvider) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {"response": "Hello world", "done": True}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await provider.generate("Hello")

    assert result == "Hello world"


@pytest.mark.asyncio
async def test_ollama_generate_structured(provider: OllamaProvider) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {"response": '{"answer": "42"}', "done": True}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await provider.generate_structured("What is life?", schema=TestModel)

    assert result == TestModel(answer="42")


@pytest.mark.asyncio
async def test_ollama_complete_with_tools(provider: OllamaProvider) -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "response": 'TOOL_CALL: {"name": "tool_name", "arguments": {"arg1": "value"}}',
        "done": True,
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    tools = [
        {
            "function": {
                "name": "tool_name",
                "description": "A test tool",
            }
        }
    ]

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await provider.complete_with_tools("Help me", tools=tools)

    assert "tool_calls" in result
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "tool_name"
    assert result["tool_calls"][0]["arguments"] == {"arg1": "value"}


def test_ollama_health_check_success(settings: Settings) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"models": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = MagicMock(return_value=mock_response)
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)

    provider = OllamaProvider(settings)

    with patch("httpx.Client", return_value=mock_client):
        provider._check_health()


def test_ollama_health_check_failure(settings: Settings) -> None:
    mock_client = MagicMock()
    mock_client.get = MagicMock(side_effect=httpx.ConnectError("Connection refused"))
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)

    provider = OllamaProvider(settings)

    with patch("httpx.Client", return_value=mock_client):
        with pytest.raises(Exception, match="Ollama not reachable"):
            provider._check_health()


@pytest.mark.asyncio
async def test_ollama_generate_timeout(provider: OllamaProvider) -> None:
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception, match="Ollama request failed"):
            await provider.generate("Hello")


@pytest.mark.asyncio
async def test_ollama_generate_connection_error(provider: OllamaProvider) -> None:
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception, match="Ollama request failed"):
            await provider.generate("Hello")


def test_ollama_provider_name(settings: Settings) -> None:
    provider = OllamaProvider(settings)
    assert provider.name == "ollama"
