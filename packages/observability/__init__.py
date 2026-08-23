"""Observability foundation (STEP 0.10).

Correlation IDs + structured JSON logging. No Grafana/Loki required in Phase 0.
"""

from packages.observability.context import (
    RequestContext,
    clear_context,
    get_context,
    set_context,
)
from packages.observability.logging import configure_logging, get_logger

__all__ = [
    "RequestContext",
    "clear_context",
    "configure_logging",
    "get_context",
    "get_logger",
    "set_context",
]
