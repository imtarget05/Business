"""Unit tests for the embedding providers (Feature 1)."""

from __future__ import annotations

import httpx
import pytest

from packages.config.settings import Settings
from packages.llm.embeddings import (
    CloudflareEmbeddingProvider,
    MockEmbeddingProvider,
    OllamaEmbeddingProvider,
    _parse_vector,
    _vector_to_pg,
    cosine_similarity,
)
from packages.database.models import EMBEDDING_DIMENSIONS


async def test_mock_returns_correct_dimension() -> None:
    provider = MockEmbeddingProvider(dim=768)
    vectors = await provider.embed(["any text"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 768 == EMBEDDING_DIMENSIONS


async def test_mock_is_deterministic() -> None:
    provider = MockEmbeddingProvider(dim=64)
    a = await provider.embed(["same input"])
    b = await provider.embed(["same input"])
    assert a == b


async def test_mock_batch_preserves_order_and_count() -> None:
    provider = MockEmbeddingProvider(dim=32)
    vectors = await provider.embed(["alpha", "beta", "gamma"])
    assert len(vectors) == 3
    singles = [await provider.embed([t]) for t in ["alpha", "beta", "gamma"]]
    for batch_vec, single in zip(vectors, singles, strict=True):
        assert batch_vec == single[0]


def test_ollama_provider_constructs_with_default_model() -> None:
    # Explicit embedding_model documents the bge-m3 default for embeddings.
    settings = Settings(ollama_base_url="http://localhost:11434", embedding_model="bge-m3")
    provider = OllamaEmbeddingProvider(settings)
    assert provider.name == "ollama_embedding"
    assert provider.model == "bge-m3"


def test_ollama_provider_falls_back_to_llm_model() -> None:
    # When no embedding model is configured, the chat model is NOT appropriate;
    # the provider must still expose a usable embedding model name.
    settings = Settings(ollama_base_url="http://localhost:11434")
    provider = OllamaEmbeddingProvider(settings)
    assert isinstance(provider.model, str) and provider.model


async def test_ollama_provider_embed_mocked() -> None:
    settings = Settings(ollama_base_url="http://localhost:11434", embedding_model="bge-m3")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        body = request.read()
        assert b'"input"' in body
        return httpx.Response(200, json={"embeddings": [[0.1] * 768, [0.2] * 768]})

    provider = OllamaEmbeddingProvider(settings, transport=httpx.MockTransport(handler))
    vectors = await provider.embed(["alpha", "beta"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 768
    assert len(vectors[1]) == 768
    assert vectors[0][0] == 0.1
    assert vectors[1][0] == 0.2
    await provider.aclose()


async def test_ollama_provider_embed_falls_back_to_legacy_endpoint() -> None:
    settings = Settings(ollama_base_url="http://localhost:11434", embedding_model="bge-m3")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(404, json={"error": "not found"})
        assert request.url.path == "/api/embeddings"
        body = request.read()
        assert b'"prompt"' in body
        return httpx.Response(200, json={"embedding": [0.1] * 768})

    provider = OllamaEmbeddingProvider(settings, transport=httpx.MockTransport(handler))
    vectors = await provider.embed(["alpha", "beta"])
    assert len(vectors) == 2
    assert all(len(v) == 768 for v in vectors)
    await provider.aclose()


def test_cloudflare_provider_requires_credentials() -> None:
    settings = Settings(cloudflare_account_id=None, cloudflare_api_token=None)
    with pytest.raises(Exception):
        CloudflareEmbeddingProvider(settings)


async def test_cloudflare_provider_embed_mocked() -> None:
    settings = Settings(
        cloudflare_account_id="test-account",
        cloudflare_api_token="test-token",
        embedding_model="@cf/baai/bge-base-en-v1.5",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {"data": [[0.9, 0.1], [0.2, 0.8]], "shape": [2, 2]},
            },
        )

    transport = httpx.MockTransport(handler)
    provider = CloudflareEmbeddingProvider(settings, transport=transport)
    vectors = await provider.embed(["alpha", "beta"])
    assert vectors == [[0.9, 0.1], [0.2, 0.8]]


def test_cosine_similarity_basic() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_vector_pg_roundtrip() -> None:
    literal = _vector_to_pg([0.1, 0.2, 0.3])
    assert literal == "[0.1,0.2,0.3]"
    assert _parse_vector(literal) == [0.1, 0.2, 0.3]
    assert _parse_vector(None) is None
