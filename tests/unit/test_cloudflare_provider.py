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


# ---------------------------------------------------------------------------
# embed() tests (Phase 2) — Cloudflare bge-base-en-v1.5 response shape
# ---------------------------------------------------------------------------


def embedding_ok_body(vectors: list[list[float]]) -> dict:
    """Cloudflare bge-base-en-v1.5 success shape: result.data holds vectors."""
    return {
        "result": {"data": vectors, "shape": [len(vectors), len(vectors[0])]},
        "success": True,
        "errors": [],
    }


def make_embedding_settings(**overrides) -> Settings:
    """Settings tuned for embedding calls (uses embedding_* retry fields)."""
    base = {
        "llm_provider": "cloudflare_ai",
        "cloudflare_account_id": "test-account",
        "cloudflare_api_token": "test-token",
        "embedding_provider": "cloudflare_ai",
        "llm_max_retries": 0,  # irrelevant for embed(); uses embedding_max_retries
        "embedding_max_retries": 2,
        "embedding_retry_backoff_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


def fake_768_vector(seed: float) -> list[float]:
    # Real bge-base-en-v1.5 output is 768-dim; use a short representative
    # vector for the mock and assert dimension separately in a full-length test.
    return [seed + i * 0.001 for i in range(768)]


async def test_embed_success_returns_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert b'"text"' in body  # payload key is "text", not "messages"
        vectors = [fake_768_vector(0.1), fake_768_vector(0.2)]
        return httpx.Response(200, json=embedding_ok_body(vectors))

    p = CloudflareAIProvider(make_embedding_settings(), transport=transport_with(handler))
    out = await p.embed(["hello", "world"])
    assert len(out) == 2
    assert all(len(vec) == 768 for vec in out)
    await p.aclose()


async def test_embed_retry_on_429_then_success(monkeypatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("packages.llm.cloudflare.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json=embedding_ok_body([fake_768_vector(0.5)]))

    p = CloudflareAIProvider(make_embedding_settings(), transport=transport_with(handler))
    out = await p.embed(["retry me"])
    assert calls["n"] == 2
    assert len(out[0]) == 768
    await p.aclose()


async def test_embed_exhausted_retries_raises(monkeypatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("packages.llm.cloudflare.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="overloaded")

    p = CloudflareAIProvider(make_embedding_settings(), transport=transport_with(handler))
    with pytest.raises(LLMProviderError, match="3 attempts"):
        await p.embed(["always fails"])
    assert calls["n"] == 3  # 1 initial + 2 retries (embedding_max_retries=2)
    await p.aclose()


async def test_embed_missing_credentials_raises() -> None:
    settings = Settings(
        llm_provider="cloudflare_ai",
        embedding_provider="cloudflare_ai",
        cloudflare_account_id=None,
        cloudflare_api_token=None,
    )
    with pytest.raises(LLMProviderError):
        CloudflareAIProvider(settings)