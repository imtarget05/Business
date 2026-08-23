"""Cloudflare provider tests — all HTTP mocked via httpx.MockTransport.

No real network calls, no credentials in CI. Verifies:
  - success path returns the model text,
  - generate_structured validates against the schema,
  - transient failures (5xx / timeout) retry with exponential backoff and
    succeed on recovery, raising LLMProviderError when retries are exhausted,
  - non-transient 4xx fails immediately without retrying.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from packages.config.settings import Settings
from packages.core.errors import LLMProviderError
from packages.llm.cloudflare import CloudflareAIProvider


def make_settings(**overrides) -> Settings:
    return Settings(
        llm_provider="cloudflare_ai",
        cloudflare_account_id="test-account",
        cloudflare_api_token="test-token",
        **overrides,
    )


def ok_body(text: str = "hello") -> dict:
    return {"success": True, "result": {"response": text}, "errors": []}


def transport_with(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class Answer(BaseModel):
    reply: str


async def test_missing_credentials_raises() -> None:
    with pytest.raises(LLMProviderError):
        CloudflareAIProvider(Settings(llm_provider="cloudflare_ai"))


async def test_generate_success() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=ok_body("hi there"))

    p = CloudflareAIProvider(make_settings(), transport=transport_with(handler))
    out = await p.generate("ping")
    assert out == "hi there"
    assert seen["auth"] == "Bearer test-token"
    assert "accounts/test-account/ai/run/" in seen["url"]
    await p.aclose()


async def test_generate_structured_validates_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok_body('{"reply": "ok"}'))

    p = CloudflareAIProvider(make_settings(), transport=transport_with(handler))
    answer = await p.generate_structured("q", Answer)
    assert answer == Answer(reply="ok")
    await p.aclose()


async def test_generate_structured_bad_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok_body("not json"))

    p = CloudflareAIProvider(make_settings(), transport=transport_with(handler))
    with pytest.raises(LLMProviderError):
        await p.generate_structured("q", Answer)
    await p.aclose()


async def test_transient_500_recovers_after_retry(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("packages.llm.cloudflare.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:  # fail twice with 503, then recover
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json=ok_body("recovered"))

    p = CloudflareAIProvider(
        make_settings(llm_max_retries=3, llm_retry_backoff_seconds=0.0),
        transport=transport_with(handler),
    )
    assert await p.generate("ping") == "recovered"
    assert calls["n"] == 3
    # Exponential backoff: 0 * 2^0, 0 * 2^1 -> both zero but recorded.
    assert len(sleeps) == 2
    await p.aclose()


async def test_retries_exhausted_raises(monkeypatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("packages.llm.cloudflare.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    p = CloudflareAIProvider(
        make_settings(llm_max_retries=2, llm_retry_backoff_seconds=0.0),
        transport=transport_with(handler),
    )
    with pytest.raises(LLMProviderError, match="3 attempts"):
        await p.generate("ping")
    assert calls["n"] == 3  # 1 initial + 2 retries
    await p.aclose()


async def test_timeout_is_retried_and_eventually_raises(monkeypatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("packages.llm.cloudflare.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    p = CloudflareAIProvider(
        make_settings(llm_max_retries=1, llm_retry_backoff_seconds=0.0),
        transport=transport_with(handler),
    )
    with pytest.raises(LLMProviderError):
        await p.generate("ping")
    await p.aclose()


async def test_non_transient_4xx_does_not_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    p = CloudflareAIProvider(
        make_settings(llm_max_retries=5), transport=transport_with(handler)
    )
    with pytest.raises(LLMProviderError, match="401"):
        await p.generate("ping")
    assert calls["n"] == 1
    await p.aclose()


async def test_api_success_false_raises_immediately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"success": False, "errors": [{"code": 1234, "message": "bad model"}]}
        return httpx.Response(200, json=body)

    p = CloudflareAIProvider(
        make_settings(llm_max_retries=3), transport=transport_with(handler)
    )
    with pytest.raises(LLMProviderError, match="bad model"):
        await p.generate("ping")
    await p.aclose()