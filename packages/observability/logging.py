"""Structured JSON logging with automatic correlation fields."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from packages.observability.context import get_context

_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": "orchestrator",
            "logger": record.name,
            "event": record.getMessage(),
        }
        ctx = get_context()
        payload["request_id"] = ctx.request_id
        if ctx.trace_id:
            payload["trace_id"] = ctx.trace_id
        if ctx.task_id:
            payload["task_id"] = str(ctx.task_id)
        if ctx.agent_run_id:
            payload["agent_run_id"] = str(ctx.agent_run_id)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    """Return a logger; correlation fields are injected by JsonFormatter."""
    return logging.LoggerAdapter(logging.getLogger(name), {})
