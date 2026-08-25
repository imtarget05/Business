"""MockEmbeddingProvider — deterministic, credential-free embeddings.

Phase 2 uses a mock provider by default to avoid network dependencies in tests
and local dev. Embeddings are deterministic hash-based vectors of fixed dimension
(768), matching the pgvector column requirement for the Cloudflare
@cf/baai/bge-base-en-v1.5 model.
"""

from __future__ import annotations

import hashlib

from packages.llm.base import EmbeddingProvider


def _hash_to_vector(text: str, dim: int) -> list[float]:
    """Deterministic hash -> vector of floats.

    Uses SHA-256 for good distribution; simple approach for consistency across runs.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    # Convert bytes to floats in range [-1.0, 1.0]
    vec = []
    for i in range(dim):
        # Use bytes from hash, combine to form a 32-bit integer for numeric stability
        byte_idx = (i * 4) % len(h)
        val = int.from_bytes(h[byte_idx:byte_idx + 4], byteorder="little", signed=False)
        # Normalize to [-1.0, 1.0]
        vec.append((val / 2**32 - 0.5) * 2.0)
    return vec


class MockEmbeddingProvider(EmbeddingProvider):
    """Scriptable in-memory embedding provider.

    Optionally feed scripted outputs via `script()`; each call consumes the next
    script entry. Unscripted calls return deterministic hashes.
    """

    def __init__(self, dim: int = 768) -> None:
        self._script: list[list[float]] = []
        self.dim = dim
        self.calls: list[list[str]] = []

    @property
    def name(self) -> str:
        return "mock_embedding"

    def script(self, *vectors: list[float]) -> None:
        self._script.extend(vectors)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self._script:
            vectors = []
            for text in texts:
                if self._script:
                    vectors.append(self._script.pop(0))
                else:
                    vectors.append(_hash_to_vector(text, self.dim))
            return vectors
        return [_hash_to_vector(text, self.dim) for text in texts]

    async def aclose(self) -> None:
        self._script.clear()
        self.calls.clear()


__all__ = ["MockEmbeddingProvider"]