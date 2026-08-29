"""Web tools abstraction (ADR-008).

Provides ``web_search`` / ``web_extract`` capabilities behind a provider
interface, mirroring the ``LLMProvider`` pattern (ADR-005). Hermes
(``hermes_tools``) is an *optional* implementation — the system runs
standalone via the httpx fallback, then mock.

Response shapes (kept compatible with hermes_tools):
- ``web_search``  -> ``{"data": {"web": [...]}}``
- ``web_extract`` -> ``{"results": [...]}``
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = [
    "WebToolsProvider",
    "HermesWebTools",
    "HttpxWebTools",
    "MockWebTools",
    "create_web_tools",
]


class WebToolsProvider(Protocol):
    """Interface every web tools implementation must satisfy."""

    async def web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Return ``{"data": {"web": [...]}}`` for the query."""
        ...

    async def web_extract(self, urls: list[str], char_limit: int = 5000) -> dict[str, Any]:
        """Return ``{"results": [...]}`` extracted content for urls."""
        ...


# ---------------------------------------------------------------------------
# Hermes implementation (optional — only available inside Hermes runtime)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - hermes_tools only present in Hermes runtime
    from hermes_tools import web_extract as _hermes_extract
    from hermes_tools import web_search as _hermes_search

    _HAS_HERMES = True
except ImportError:  # pragma: no cover
    _HAS_HERMES = False


class HermesWebTools:
    """Adapter over the optional hermes_tools runtime."""

    def __init__(self) -> None:
        if not _HAS_HERMES:
            raise RuntimeError("hermes_tools is not installed in this environment")

    async def web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        return await _hermes_search(query=query, limit=limit)  # type: ignore[misc]

    async def web_extract(self, urls: list[str], char_limit: int = 5000) -> dict[str, Any]:
        return await _hermes_extract(urls=urls[:3], char_limit=char_limit)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# httpx fallback (standalone, no hermes needed)
# ---------------------------------------------------------------------------


class HttpxWebTools:
    """DuckDuckGo HTML search + page fetch via httpx. No external agent SDK."""

    async def web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        import re
        import urllib.parse

        import httpx

        async with httpx.AsyncClient(
            timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            r = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
            r.raise_for_status()
            html = r.text
            # DuckDuckGo encodes real urls as //duckduckgo.com/l/?uddg=https%3A%2F%2F...
            uddgs = re.findall(r"uddg=([^&\"']+)", html)
            seen: set[str] = set()
            results: list[dict[str, str]] = []
            for enc in uddgs:
                try:
                    u = urllib.parse.unquote(enc)
                except Exception:
                    continue
                if not u.startswith("http") or "duckduckgo.com" in u or u in seen:
                    continue
                seen.add(u)
                host = u.split("/")[2] if len(u.split("/")) > 2 else u
                results.append({"title": f"{host} — {query[:30]}", "url": u, "snippet": query})
                if len(results) >= limit:
                    break
            # fallback if no uddg: try direct hrefs
            if not results:
                urls = re.findall(r'href="(https?://[^"]+)"', html)
                for u in urls:
                    if "duckduckgo.com" in u or u in seen:
                        continue
                    seen.add(u)
                    host = u.split("/")[2] if len(u.split("/")) > 2 else u
                    results.append({"title": host, "url": u, "snippet": query})
                    if len(results) >= limit:
                        break
        return {"data": {"web": results}}

    async def web_extract(self, urls: list[str], char_limit: int = 5000) -> dict[str, Any]:
        import httpx

        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            for u in urls[:3]:
                try:
                    r = await client.get(u)
                    r.raise_for_status()
                    content = r.text[:char_limit]
                except Exception as e:
                    content = f"[extract error: {e}]"
                results.append({"title": u, "url": u, "content": content})
        return {"results": results}


# ---------------------------------------------------------------------------
# Mock implementation (deterministic, zero network)
# ---------------------------------------------------------------------------


class MockWebTools:
    """Deterministic offline implementation — used in tests and default dev."""

    async def web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        return {
            "data": {
                "web": [
                    {"title": f"Mock result for {query}", "url": "https://example.com", "snippet": "mock"}
                ]
            }
        }

    async def web_extract(self, urls: list[str], char_limit: int = 5000) -> dict[str, Any]:
        return {
            "results": [
                {"title": u, "url": u, "content": f"[mock content for {u}]"} for u in urls[:3]
            ]
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "hermes": HermesWebTools,
    "httpx": HttpxWebTools,
    "mock": MockWebTools,
}


def create_web_tools(provider: str = "auto") -> WebToolsProvider:
    """Create a web tools provider.

    ``auto`` resolves to the best available implementation:
    hermes -> httpx -> mock. Explicit names raise if unavailable.
    """
    if provider == "auto":
        if _HAS_HERMES:  # pragma: no cover - hermes only in Hermes runtime
            return HermesWebTools()
        return HttpxWebTools()
    if provider not in _PROVIDERS:
        raise ValueError(
            f"unknown web tools provider {provider!r} (choose from {sorted(_PROVIDERS)})"
        )
    return _PROVIDERS[provider]()  # type: ignore[return-value]

