"""Phase E tests: tracing abstraction (no-op default, backend selection)."""

from __future__ import annotations

import os

from packages.core.tracing import (
    LangfuseTracer,
    NoOpTracer,
    OTelTracer,
    Tracer,
    get_tracer,
)


def test_default_tracer_is_noop() -> None:
    # Ensure backend unset
    os.environ.pop("TRACING_BACKEND", None)
    tracer = get_tracer()
    assert isinstance(tracer, NoOpTracer)
    # No-op must not raise on any operation
    sid = tracer.start_span("x", foo="bar")
    assert sid == "noop"
    tracer.end_span(sid)
    tracer.event("evt")
    with tracer.span("ctx") as s:
        assert s == "noop"


def test_noop_tracer_is_subclass_of_tracer() -> None:
    assert isinstance(NoOpTracer(), Tracer)


def test_langfuse_tracer_importable_without_sdk(monkeypatch) -> None:
    """Without langfuse SDK installed, LangfuseTracer degrades gracefully (no-op)."""
    # If langfuse happens to be installed, skip the degradation assertion but
    # still verify construction does not raise.
    try:
        import langfuse  # noqa: F401

        tracer = LangfuseTracer()
        assert isinstance(tracer, Tracer)
    except ImportError:
        tracer = LangfuseTracer()
        # Without SDK it must still be usable and never raise on operations.
        sid = tracer.start_span("x")
        tracer.end_span(sid)
        tracer.event("evt")
        assert isinstance(tracer, Tracer)


def test_otel_tracer_importable_without_sdk(monkeypatch) -> None:
    try:
        import opentelemetry  # noqa: F401

        tracer = OTelTracer()
        assert isinstance(tracer, Tracer)
    except ImportError:
        tracer = OTelTracer()
        # Without SDK, it should still be usable (records nothing)
        sid = tracer.start_span("x")
        tracer.end_span(sid)
        assert isinstance(tracer, Tracer)


def test_get_tracer_backend_selection(monkeypatch) -> None:
    monkeypatch.setenv("TRACING_BACKEND", "langfuse")
    tracer = get_tracer()
    assert isinstance(tracer, (LangfuseTracer, NoOpTracer))
    monkeypatch.setenv("TRACING_BACKEND", "unknown_backend")
    assert isinstance(get_tracer(), NoOpTracer)
