"""MockLLMProvider + provider factory + error model tests."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from packages.config.settings import LLMProviderKind, Settings
from packages.core.errors import HTTP_STATUS_BY_CODE, AgentTimeoutError, BusinessOpsError
from packages.llm.factory import get_llm_provider
from packages.llm.mock import MockLLMProvider


class Classification(BaseModel):
    domain: str
    action: str


@pytest.mark.asyncio
async def test_mock_generate_default() -> None:
    provider = MockLLMProvider()
    text = await provider.generate("hello")
    assert "mock" in text.lower()


@pytest.mark.asyncio
async def test_mock_structured_scripted() -> None:
    provider = MockLLMProvider(scripted=[{"domain": "support", "action": "triage"}])
    out = await provider.generate_structured("classify", Classification)
    assert out == Classification(domain="support", action="triage")


@pytest.mark.asyncio
async def test_mock_unscripted_structured_raises() -> None:
    provider = MockLLMProvider()
    with pytest.raises(ValueError):
        await provider.generate_structured("classify", Classification)


def test_factory_selects_by_settings() -> None:
    s = Settings(llm_provider=LLMProviderKind.MOCK)
    assert isinstance(get_llm_provider(s), MockLLMProvider)


def test_error_payload_shape() -> None:
    err = AgentTimeoutError("Support agent timed out", details={"agent": "support-v1"})
    payload = err.to_payload()
    assert payload["code"] == "AGENT_TIMEOUT"
    assert HTTP_STATUS_BY_CODE[err.code] == 504
    assert isinstance(err, BusinessOpsError)
