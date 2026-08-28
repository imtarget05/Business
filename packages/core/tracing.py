# -*- coding: utf-8 -*-
"""Tracing / observability abstraction (Phase E).

Provides a no-op-by-default tracer that can be swapped for Langfuse or
OpenTelemetry by setting environment variables. The system MUST run without any
tracing backend (ADR-001), so the default implementation records nothing and
never raises.

Tracer selection:
- TRACING_BACKEND=langfuse  -> LangfuseTracer (requires langfuse SDK + env keys)
- TRACING_BACKEND=otel      -> OTelTracer (requires opentelemetry SDK)
- (unset / other)            -> NoOpTracer
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


class Tracer(ABC):
    """Minimal tracing contract used across agents."""

    @abstractmethod
    def start_span(self, name: str, **attributes: Any) -> str:
        """Return a span id (string)."""

    @abstractmethod
    def end_span(self, span_id: str, **attributes: Any) -> None:
        """End a span by id."""

    @abstractmethod
    def event(self, name: str, **attributes: Any) -> None:
        """Emit a discrete event."""

    @contextmanager
    def span(self, name: str, **attributes: Any):
        sid = self.start_span(name, **attributes)
        try:
            yield sid
        finally:
            self.end_span(sid)


class NoOpTracer(Tracer):
    """Default tracer — records nothing, never fails."""

    def start_span(self, name: str, **attributes: Any) -> str:
        return "noop"

    def end_span(self, span_id: str, **attributes: Any) -> None:
        pass

    def event(self, name: str, **attributes: Any) -> None:
        pass


class LangfuseTracer(Tracer):
    """Langfuse-backed tracer. Imported lazily so missing SDK never breaks boot."""

    def __init__(self, public_key: str | None = None, secret_key: str | None = None,
                 host: str | None = None) -> None:
        try:
            from langfuse import Langfuse  # type: ignore
        except ImportError:
            logger.warning("langfuse SDK not installed; falling back to NoOpTracer")
            self._impl: Tracer = NoOpTracer()
            return
        self._impl = Langfuse(  # type: ignore[operator]
            public_key=public_key or os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=secret_key or os.environ.get("LANGFUSE_SECRET_KEY"),
            host=host or os.environ.get("LANGFUSE_HOST"),
        )

    def start_span(self, name: str, **attributes: Any) -> str:
        try:
            span = self._impl.start_span(name=name, metadata=attributes)
            return getattr(span, "id", "langfuse")
        except Exception as e:  # never crash the app on tracing errors
            logger.debug(f"langfuse start_span failed: {e}")
            return "langfuse"

    def end_span(self, span_id: str, **attributes: Any) -> None:
        try:
            self._impl.flush()
        except Exception:
            pass

    def event(self, name: str, **attributes: Any) -> None:
        try:
            self._impl.event(name=name, metadata=attributes)
        except Exception:
            pass


class OTelTracer(Tracer):
    """OpenTelemetry-backed tracer. Imported lazily."""

    def __init__(self) -> None:
        try:
            from opentelemetry import trace as otel_trace  # type: ignore
        except ImportError:
            logger.warning("opentelemetry SDK not installed; falling back to NoOpTracer")
            self._tracer = None
            return
        self._tracer = otel_trace.get_tracer("business_ops_agent")

    def start_span(self, name: str, **attributes: Any) -> str:
        if self._tracer is None:
            return "otel"
        span = self._tracer.start_span(name)
        for k, v in attributes.items():
            span.set_attribute(k, str(v))
        return str(id(span))

    def end_span(self, span_id: str, **attributes: Any) -> None:
        pass

    def event(self, name: str, **attributes: Any) -> None:
        pass


def get_tracer() -> Tracer:
    """Factory: select tracer from TRACING_BACKEND env (default NoOp)."""
    backend = os.environ.get("TRACING_BACKEND", "").lower()
    if backend == "langfuse":
        return LangfuseTracer()
    if backend == "otel":
        return OTelTracer()
    return NoOpTracer()


__all__ = ["Tracer", "NoOpTracer", "LangfuseTracer", "OTelTracer", "get_tracer"]
