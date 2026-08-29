"""Shared tool abstractions (web tools, ADR-008). Hermes is optional."""

from packages.tools.web import (
    HermesWebTools,
    HttpxWebTools,
    MockWebTools,
    WebToolsProvider,
    create_web_tools,
)

__all__ = [
    "HermesWebTools",
    "HttpxWebTools",
    "MockWebTools",
    "WebToolsProvider",
    "create_web_tools",
]
