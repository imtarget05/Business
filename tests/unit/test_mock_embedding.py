"""Unit tests for MockEmbeddingProvider (Phase 2).

Asserts:
  - determinism: same input text -> identical vector across calls
  - distinct inputs -> distinct vectors
  - vectors are exactly EMBEDDING_DIMENSIONS long (768)
"""

from __future__ import annotations

import pytest

from packages.database.models import EMBEDDING_DIMENSIONS
from packages.llm.mock_embedding import MockEmbeddingProvider


@pytest.fixture()
def provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider()


async def test_same_text_returns_identical_vector(provider: MockEmbeddingProvider) -> None:
    first = await provider.embed(["refund policy"])
    second = await provider.embed(["refund policy"])
    assert first == second


async def test_different_texts_return_different_vectors(
    provider: MockEmbeddingProvider,
) -> None:
    a = await provider.embed(["shipping cost"])
    b = await provider.embed(["warranty terms"])
    assert a[0] != b[0]


async def test_vector_is_correct_dimension(
    provider: MockEmbeddingProvider,
) -> None:
    vectors = await provider.embed(["any text"])
    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIMENSIONS == 768


async def test_batch_embed_preserves_order_and_count(
    provider: MockEmbeddingProvider,
) -> None:
    texts = ["alpha", "beta", "gamma"]
    vectors = await provider.embed(texts)
    assert len(vectors) == 3
    # each vector must match its single-text equivalent (order preserved)
    singles = [await provider.embed([t]) for t in texts]
    for batch_vec, single in zip(vectors, singles, strict=True):
        assert batch_vec == single[0]


async def test_all_values_are_floats_in_unit_range(
    provider: MockEmbeddingProvider,
) -> None:
    vec = (await provider.embed(["numeric range check"]))[0]
    assert all(isinstance(v, float) and -1.0 <= v <= 1.0 for v in vec)
