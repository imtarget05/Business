# -*- coding: utf-8 -*-
"""Per-user conversation sessions for the Telegram bot (Feature 5 — UX).

Keyed by `telegram_user_id` so the context follows the *person* (a user may talk
to the bot in several chats). Pure standard library — no Redis, no external
service — so the bot imports offline and the unit tests run without network.

Expiry is *lazy*: `ttl_seconds` (default 1800 = 30 phút) is evaluated on access
instead of by a background task, which keeps the store safe to share across
event loops and free of dangling timers.

Usage:
    store = SessionStore()                 # ttl 30 phút
    store.update(user_id, last_query="tìm quán ăn Hà Nội")
    sess = store.get(user_id)              # None nếu chưa có / đã hết hạn
    store.clear(user_id)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

# 30 minutes — long enough for a multi-turn Vietnamese conversation, short
# enough that a stale "trang 3" pagination context never resurfaces.
DEFAULT_TTL_SECONDS = 1800

# Keep the tail of the conversation only (prompt-size + memory guard).
MAX_HISTORY = 20


@dataclass
class Session:
    """Minimal per-user conversation context.

    Attributes:
        last_query: câu hỏi gần nhất của user (free text, chưa chuẩn hóa).
        last_capability: capability đã route lần cuối (vd "knowledge.query").
        page: trang hiện tại của kết quả đang phân trang (0-based).
        history: các tin nhắn gần đây (mới nhất ở cuối), tối đa MAX_HISTORY.
        organization_id: tổ chức dùng cho TaskContext khi gọi agent.
        results: cache danh sách kết quả gần nhất — cần để lát (slice) trang khi
            user bấm "Tiếp ▶️" mà KHÔNG phải gọi lại web/LLM.
        updated_at: mốc thời gian (theo clock của store) cho lazy expiry.
    """

    last_query: str | None = None
    last_capability: str | None = None
    page: int = 0
    history: list[Any] = field(default_factory=list)
    organization_id: str | None = None
    results: list[Any] = field(default_factory=list)
    updated_at: float = 0.0

    def remember(self, message: Any, limit: int = MAX_HISTORY) -> None:
        """Append a message to history, trimming to the last `limit` entries."""
        self.history.append(message)
        if limit > 0 and len(self.history) > limit:
            del self.history[:-limit]


class SessionStore:
    """In-memory `telegram_user_id -> Session` map with lazy TTL expiry.

    Args:
        ttl_seconds: tuổi tối đa của một session (<= 0 nghĩa là không hết hạn).
        clock: nguồn thời gian (monotonic) — tests inject một clock giả.

    Expiry is *lazy* and *global*: `ttl_seconds` (default 1800 = 30 phut) is
    evaluated on access, but a session is only truly reclaimed once a sweep
    runs. Any read of the store size or contents (`len`, `__iter__`,
    `active_user_ids`, `purge_expired`) triggers a global sweep that drops
    *every* expired session - so users who never return still have their slots
    freed and memory does not grow unbounded. No background timer is used, which
    keeps the store safe to share across event loops.
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock: Callable[[], float] = clock or time.monotonic
        self._sessions: dict[int, Session] = {}

    # -- internals ---------------------------------------------------------

    def _now(self) -> float:
        return float(self._clock())

    def _is_expired(self, session: Session) -> bool:
        if self.ttl_seconds <= 0:
            return False
        return (self._now() - session.updated_at) > self.ttl_seconds

    def _touch(self, session: Session) -> Session:
        session.updated_at = self._now()
        return session

    # -- public API --------------------------------------------------------

    def get(self, user_id: int) -> Session | None:
        """Return the live session for `user_id`, or None if missing/expired.

        Expired entries are dropped here (lazy expiry) so memory does not grow
        for users who never come back.
        Because every read of the store also runs a global sweep (see `_sweep`), expired
        sessions for users who never come back are reclaimed too - memory does not
        grow unbounded.
        """
        session = self._sessions.get(user_id)
        if session is None:
            return None
        if self._is_expired(session):
            self._sessions.pop(user_id, None)
            return None
        return session

    def get_or_create(self, user_id: int) -> Session:
        """Return the live session, creating a fresh one when absent/expired."""
        session = self.get(user_id)
        if session is None:
            session = self._touch(Session())
            self._sessions[user_id] = session
        return session

    def set(self, user_id: int, ctx: Session) -> Session:
        """Store `ctx` for `user_id` and refresh its TTL."""
        if not isinstance(ctx, Session):
            raise TypeError("ctx must be a Session instance")
        self._sessions[user_id] = self._touch(ctx)
        return ctx

    def update(self, user_id: int, **changes: Any) -> Session:
        """Patch fields of the user's session (creating it if needed).

        Raises:
            AttributeError: nếu truyền field không tồn tại trên Session — lỗi
                gõ sai tên field phải nổ ngay chứ không âm thầm bị bỏ qua.
        """
        session = self.get_or_create(user_id)
        for key, value in changes.items():
            if not hasattr(session, key):
                raise AttributeError(f"Session has no field '{key}'")
            setattr(session, key, value)
        return self._touch(session)

    def clear(self, user_id: int) -> None:
        """Forget everything about `user_id` (used by 'session mới')."""
        self._sessions.pop(user_id, None)

    def clear_all(self) -> None:
        """Drop every session (test helper / bot restart)."""
        self._sessions.clear()

    def _sweep(self) -> None:
        """Remove every expired session from memory (global lazy TTL sweep).

        Called from `get`-free reads of the store so expired entries for users
        who never return are reclaimed, not just those who come back.
        """
        expired = [uid for uid, s in self._sessions.items() if self._is_expired(s)]
        for uid in expired:
            self._sessions.pop(uid, None)

    def purge_expired(self) -> int:
        """Drop all expired sessions; returns how many were removed."""
        before = len(self._sessions)
        self._sweep()
        return before - len(self._sessions)

    def active_user_ids(self) -> list[int]:
        """User ids with a still-live session (expired ones are swept first)."""
        self._sweep()
        return list(self._sessions.keys())

    def __contains__(self, user_id: object) -> bool:
        return isinstance(user_id, int) and self.get(user_id) is not None

    def __len__(self) -> int:
        self._sweep()
        return len(self._sessions)

    def __iter__(self) -> Iterator[int]:
        return iter(self.active_user_ids())


__all__ = ["DEFAULT_TTL_SECONDS", "MAX_HISTORY", "Session", "SessionStore"]