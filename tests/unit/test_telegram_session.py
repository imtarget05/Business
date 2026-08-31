# -*- coding: utf-8 -*-
"""Unit tests for the Telegram SessionStore (Feature 5 — UX).

Covers get / set / update / clear, history trimming and lazy TTL expiry with an
injected fake clock (no sleeps, fully offline).
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from packages.telegram.session import (
    DEFAULT_TTL_SECONDS,
    MAX_HISTORY,
    Session,
    SessionStore,
)


class FakeClock:
    """Deterministic monotonic clock so TTL tests never sleep."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(clock: FakeClock) -> SessionStore:
    return SessionStore(clock=clock)


# ---------------------------------------------------------------------------
# get / set / update / clear
# ---------------------------------------------------------------------------

def test_default_ttl_is_30_minutes(store: SessionStore):
    assert store.ttl_seconds == DEFAULT_TTL_SECONDS == 1800


def test_get_unknown_user_returns_none(store: SessionStore):
    assert store.get(42) is None
    assert 42 not in store
    assert len(store) == 0


def test_get_or_create_returns_empty_session(store: SessionStore):
    session = store.get_or_create(7)
    assert isinstance(session, Session)
    assert session.last_query is None
    assert session.last_capability is None
    assert session.page == 0
    assert session.history == []
    assert session.organization_id is None
    # Same object on the next call (context must persist).
    assert store.get_or_create(7) is session
    assert store.get(7) is session


def test_set_and_get_roundtrip(store: SessionStore):
    ctx = Session(last_query="tìm quán ăn Hà Nội", last_capability="knowledge.query", page=2)
    store.set(11, ctx)
    loaded = store.get(11)
    assert loaded is ctx
    assert loaded.last_query == "tìm quán ăn Hà Nội"
    assert loaded.last_capability == "knowledge.query"
    assert loaded.page == 2


def test_set_rejects_non_session(store: SessionStore):
    with pytest.raises(TypeError):
        store.set(1, {"last_query": "x"})  # type: ignore[arg-type]


def test_update_creates_then_patches(store: SessionStore):
    store.update(5, last_query="báo cáo hôm nay", last_capability="reporting")
    store.update(5, page=3)
    session = store.get(5)
    assert session is not None
    # Earlier fields survive a later partial update.
    assert session.last_query == "báo cáo hôm nay"
    assert session.last_capability == "reporting"
    assert session.page == 3


def test_update_rejects_unknown_field(store: SessionStore):
    with pytest.raises(AttributeError):
        store.update(5, khong_ton_tai="x")


def test_sessions_are_isolated_per_user(store: SessionStore):
    store.update(1, last_query="tìm quán ăn")
    store.update(2, last_query="báo cáo")
    assert store.get(1).last_query == "tìm quán ăn"
    assert store.get(2).last_query == "báo cáo"
    assert len(store) == 2


def test_clear_forgets_only_that_user(store: SessionStore):
    store.update(1, last_query="a")
    store.update(2, last_query="b")
    store.clear(1)
    assert store.get(1) is None
    assert store.get(2) is not None
    store.clear(999)  # clearing an unknown user must not raise


def test_clear_all(store: SessionStore):
    store.update(1, last_query="a")
    store.update(2, last_query="b")
    store.clear_all()
    assert len(store) == 0


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def test_remember_keeps_order_and_trims(store: SessionStore):
    session = store.get_or_create(3)
    for i in range(MAX_HISTORY + 5):
        session.remember(f"tin nhắn {i}")
    assert len(session.history) == MAX_HISTORY
    # Oldest dropped, newest kept last.
    assert session.history[-1] == f"tin nhắn {MAX_HISTORY + 4}"
    assert session.history[0] == f"tin nhắn {5}"


def test_remember_respects_custom_limit():
    session = Session()
    session.remember("a", limit=2)
    session.remember("b", limit=2)
    session.remember("c", limit=2)
    assert session.history == ["b", "c"]


# ---------------------------------------------------------------------------
# lazy TTL expiry
# ---------------------------------------------------------------------------

def test_session_expires_after_ttl(store: SessionStore, clock: FakeClock):
    store.update(1, last_query="tìm quán ăn")
    clock.advance(store.ttl_seconds - 1)
    assert store.get(1) is not None, "must survive just under the TTL"
    clock.advance(2)
    assert store.get(1) is None, "must expire just over the TTL"


def test_expired_entry_is_dropped_from_memory(store: SessionStore, clock: FakeClock):
    store.update(1, last_query="x")
    clock.advance(store.ttl_seconds + 1)
    assert store.get(1) is None
    # Lazy expiry actually frees the slot.
    assert len(store) == 0
    assert store.active_user_ids() == []


def test_activity_extends_the_ttl(store: SessionStore, clock: FakeClock):
    store.update(1, last_query="lượt 1")
    clock.advance(store.ttl_seconds - 10)
    store.update(1, last_query="lượt 2")  # touch refreshes updated_at
    clock.advance(store.ttl_seconds - 10)
    session = store.get(1)
    assert session is not None
    assert session.last_query == "lượt 2"


def test_get_or_create_after_expiry_returns_fresh_session(
    store: SessionStore, clock: FakeClock
):
    store.update(1, last_query="cũ", page=4)
    clock.advance(store.ttl_seconds + 1)
    session = store.get_or_create(1)
    assert session.last_query is None
    assert session.page == 0


def test_purge_expired_counts_removed(store: SessionStore, clock: FakeClock):
    store.update(1, last_query="a")
    store.update(2, last_query="b")
    clock.advance(store.ttl_seconds + 1)
    store.update(3, last_query="c")  # fresh
    assert store.purge_expired() == 2
    assert store.active_user_ids() == [3]


def test_zero_ttl_disables_expiry(clock: FakeClock):
    store = SessionStore(ttl_seconds=0, clock=clock)
    store.update(1, last_query="mãi mãi")
    clock.advance(10_000_000)
    assert store.get(1) is not None


def test_custom_ttl_is_respected(clock: FakeClock):
    store = SessionStore(ttl_seconds=60, clock=clock)
    store.update(1, last_query="x")
    clock.advance(61)
    assert store.get(1) is None


def test_iteration_yields_live_users(store: SessionStore, clock: FakeClock):
    store.update(1, last_query="a")
    clock.advance(store.ttl_seconds + 1)
    store.update(2, last_query="b")
    assert list(store) == [2]