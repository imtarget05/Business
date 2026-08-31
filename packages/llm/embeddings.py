"""Embedding providers for semantic (vector) search - Feature 1.

Defines concrete implementations of the ``EmbeddingProvider`` protocol:

- MockEmbeddingProvider - deterministic, credential-free (offline).
- OllamaEmbeddingProvider - local Ollama /api/embed (e.g. bge-m3).
- CloudflareEmbeddingProvider - Cloudflare Workers AI embeddings endpoint.

The canonical ``EmbeddingProvider`` protocol is imported from
``packages.llm.base`` (single source of truth) and re-exported here.
``MockEmbeddingProvider`` is re-exported from ``packages.llm.mock_embedding`` so
existing tests keep importing it there.
"""

from __future__ import annotations

import asyncio
import math

import httpx

from packages.config.settings import Settings
from packages.llm.base import EmbeddingProvider, provider_error
from packages.llm.mock_embedding import (  # re-export
    MockEmbeddingProvider as MockEmbeddingProvider,
)


def _vector_to_pg(vector: list[float]) -> str:
    """Serialize a vector to the pgvector text literal ``[v0, v1, ...]``.

    The same literal is accepted by a native ``vector`` column (PostgreSQL +
    pgvector) and by a plain ``TEXT`` column (SQLite fallback), so a single
    store/parse path works on every dialect.
    """
    return "[" + ",".join(f"{v:.8g}" for v in vector) + "]"


def _parse_vector(value: object) -> list[float] | None:
    """Parse a stored embedding back into a list of floats.

    Accepts a pgvector literal ``[0.1,0.2]``, a JSON array, or ``None``.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "NULL":
        return None
    if text.startswith("["):
        text = text[1:]
    if text.endswith("]"):
        text = text[:-1]
    if not text:
        return None
    try:
        return [float(x) for x in text.split(",")]
    except ValueError:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors in [-1, 1].

    Returns 0.0 when either vector has zero magnitude (undefined angle).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class OllamaEmbeddingProvider:
    """Local embeddings via Ollama's ``/api/embed`` endpoint.

    Default model is ``bge-m3`` (override via ``EMBEDDING_MODEL`` /
    ``settings.embedding_model``). The chat model (``LLM_MODEL``) is NEVER used
    for embeddings because chat models emit a vector dimension that does not
    match the configured ``vector`` column.

    The provider is fully constructible offline (no network at init). A single
    ``httpx.AsyncClient`` is created for the provider's lifetime and must be
    released via ``aclose()``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: "httpx.AsyncBaseTransport | None" = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.ollama_base_url or "http://localhost:11434"
        # NEVER fall back to the chat (LLM) model for embeddings.
        self.model = settings.embedding_model or "bge-m3"
        # Expected embedding dimension; validated against real responses.
        self._dim = (
            settings.embedding_dimensions
            or getattr(settings, "embedding_dim", None)
            or 768
        )
        self._timeout = httpx.Timeout(
            settings.embedding_request_timeout_seconds, connect=5.0
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=transport
        )

    @property
    def name(self) -> str:
        return "ollama_embedding"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await self._embed_batch(texts)
        if vectors is None:
            vectors = await self._embed_loop(texts)
        if len(vectors) != len(texts):
            raise provider_error(
                self.name,
                f"embedding count mismatch: expected {len(texts)}, got {len(vectors)}",
            )
        self._assert_dimensions(vectors)
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Try the modern batch endpoint ``/api/embed`` (key ``embeddings``)."""
        try:
            resp = await self._client.post(
                "/api/embed", json={"model": self.model, "input": texts}
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        data = resp.json()
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            return None
        return [[float(v) for v in vec] for vec in embeddings]

    async def _embed_loop(self, texts: list[str]) -> list[list[float]]:
        """Fallback: loop the legacy ``/api/embeddings`` endpoint per text."""
        vectors: list[list[float]] = []
        try:
            for text in texts:
                resp = await self._client.post(
                    "/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                embedding = data.get("embedding")
                if not isinstance(embedding, list):
                    raise provider_error(
                        self.name, f"unexpected embeddings response: {data!r}"
                    )
                vectors.append([float(v) for v in embedding])
        except httpx.HTTPError as exc:  # noqa: BLE001
            raise provider_error(self.name, f"Ollama embeddings failed: {exc}")
        return vectors

    def _assert_dimensions(self, vectors: list[list[float]]) -> None:
        if not self._dim:
            return
        mismatched = {len(v) for v in vectors if len(v) != self._dim}
        if mismatched:
            raise provider_error(
                self.name,
                f"embedding dimension mismatch: configured {self._dim}, "
                f"got dimensions {sorted(mismatched)}",
            )

    async def aclose(self) -> None:
        await self._client.aclose()


class CloudflareEmbeddingProvider:
    """Cloudflare Workers AI embeddings (``@cf/baai/bge-base-en-v1.5``).

    Mirrors the retry/backoff policy of CloudflareAIProvider but talks only to
    the embeddings model path. The HTTP transport is injectable so the provider
    can be exercised without network access or credentials.
    """

    EMBEDDING_MODEL = "@cf/baai/bge-base-en-v1.5"  # 768-dim output

    def __init__(
        self,
        settings: Settings,
        *,
        transport: "httpx.AsyncBaseTransport | None" = None,
    ) -> None:
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
            raise provider_error(
                self.name,
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be set "
                "for the Cloudflare embedding provider",
            )
        self._settings = settings
        self.model = settings.embedding_model or self.EMBEDDING_MODEL
        self._retries = max(0, settings.embedding_max_retries)
        self._backoff = settings.embedding_retry_backoff_seconds
        self._timeout = settings.embedding_request_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=(
                "https://api.cloudflare.com/client/v4/accounts/"
                f"{settings.cloudflare_account_id}/ai/run/"
            ),
            headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
            timeout=self._timeout,
            transport=transport,
        )

    @property
    def name(self) -> str:
        return "cloudflare_embedding"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        last_detail = "unknown error"
        for attempt in range(self._retries + 1):
            try:
                resp = await self._client.post(self.model, json={"text": texts})
            except httpx.TimeoutException:
                last_detail = f"request timed out after {self._timeout}s"
            except httpx.HTTPError as exc:  # noqa: BLE001
                last_detail = f"HTTP error: {type(exc).__name__}: {exc}"
            else:
                if resp.status_code >= 400:
                    last_detail = f"HTTP {resp.status_code}"
                else:
                    body = resp.json()
                    if not body.get("success"):
                        errors = "; ".join(str(e) for e in body.get("errors", []))
                        raise provider_error(
                            self.name, f"API failure: {errors or body!r}"
                        )
                    data = body.get("result", {}).get("data")
                    if not isinstance(data, list) or not data:
                        raise provider_error(
                            self.name, f"unexpected shape: {body!r}"
                        )
                    return [[float(v) for v in vec] for vec in data]
            if attempt < self._retries:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise provider_error(
            self.name,
            f"failed after {self._retries + 1} attempts ({last_detail})",
        )

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = [
    "EmbeddingProvider",
    "MockEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "CloudflareEmbeddingProvider",
    "cosine_similarity",
    "_vector_to_pg",
    "_parse_vector",
]
