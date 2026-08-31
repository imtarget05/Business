"""Correlation context propagated via contextvars (request/task/trace scope)."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID


@dataclass
class RequestContext:
    request_id: str
    trace_id: str | None = None
    task_id: UUID | None = None
    agent_run_id: UUID | None = None


_current: ContextVar[RequestContext | None] = ContextVar("boas_request_context", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _current.set(ctx)


def reset_context(token: Token[RequestContext | None]) -> None:
    _current.reset(token)


def get_context() -> RequestContext:
    ctx = _current.get()
    if ctx is None:
        ctx = RequestContext(request_id=new_request_id())
        _current.set(ctx)
    return ctx


def clear_context() -> None:
    _current.set(None)
