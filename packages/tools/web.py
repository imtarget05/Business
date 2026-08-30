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



_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi,en;q=0.8",
}


def _html_to_text(html: str) -> str:
    import re
    from html import unescape

    html = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr|/td)[^>]*>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _bing_rss_parse(xml_text: str, limit: int) -> list[dict[str, str]]:
    """Parse Bing RSS (<item><title/><link/><description/>) into search results."""
    import re
    from html import unescape

    out: list[dict[str, str]] = []
    for item in re.findall(r"<item>(.*?)</item>", xml_text, re.S)[:limit]:
        def _tag(name: str) -> str:
            m = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
            return _html_to_text(unescape(m.group(1))) if m else ""
        title = _tag("title")
        link = _tag("link")
        snippet = _tag("description")
        if not link.startswith("http"):
            continue
        out.append({"title": title or link, "url": link, "snippet": snippet})
    return out


class HttpxWebTools:
    """Multi-engine search (Bing RSS primary, DuckDuckGo fallback) + extraction.

    Search engines block datacenter/abused IPs with anti-bot interstitials
    (DDG returns 202, r.jina.ai returns 403 Cloudflare). Bing RSS has proven
    reliable, so it is tried first; DDG stays as a secondary source.
    """

    async def web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        import re
        import urllib.parse

        import httpx

        results: list[dict[str, str]] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_BROWSER_HEADERS) as client:
            # --- Primary: DuckDuckGo html/lite (returns real results for vi-VN;
            #     Bing RSS frequently answers off-topic/abused-IP interstitials
            #     for Vietnamese queries, so it is only a secondary fallback) ---
            for endpoint in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
                try:
                    r = await client.get(endpoint, params={"q": query})
                except Exception:
                    continue
                if r.status_code != 200:
                    continue
                html = r.text
                if "www.bing.com" in str(r.url) or "<rss" in html[:200].lower():
                    import urllib.parse as _up
                    import xml.etree.ElementTree as _ET
                    try:
                        root = _ET.fromstring(html)
                        for item in root.iter("item"):
                            t_el = item.find("title")
                            l_el = item.find("link")
                            d_el = item.find("description")
                            u = (l_el.text or "").strip() if l_el is not None else ""
                            if not u.startswith("http") or u in {x["url"] for x in results}:
                                continue
                            results.append({
                                "title": (t_el.text or "").strip() if t_el is not None else u,
                                "url": u,
                                "snippet": _html_to_text(d_el.text or "") if d_el is not None else "",
                            })
                            if len(results) >= limit:
                                break
                    except _ET.ParseError:
                        pass
                    if results:
                        break
                    continue
                links = re.findall(
                    r'<a[^>]+href="([^"]*uddg=([^&"]+[^"]*))"[^>]*>(.*?)</a>', html, re.S
                )
                if not links:
                    continue
                snippet_re = re.compile(
                    r'(?:result-snippet|result__snippet)[^>]*>(.*?)</(?:td|a)>', re.S
                )
                snippets = [_html_to_text(sn) for sn in snippet_re.findall(html)]
                seen: set[str] = set()
                for i, (_href, enc, title) in enumerate(links):
                    try:
                        u = urllib.parse.unquote(enc)
                    except Exception:
                        continue
                    if not u.startswith("http") or "duckduckgo.com" in u or u in seen:
                        continue
                    seen.add(u)
                    host = u.split("/")[2] if len(u.split("/")) > 2 else u
                    t = _html_to_text(title) or host
                    sn = snippets[i] if i < len(snippets) else ""
                    if not sn or sn.lower() == query.lower():
                        sn = t
                    results.append({"title": t, "url": u, "snippet": sn})
                    if len(results) >= limit:
                        break
                if results:
                    break
            if results:
                return {"data": {"web": results}}

            # --- Fallback: Bing RSS (deterministic XML) ---
            try:
                r = await client.get(
                    "https://www.bing.com/search",
                    params={"q": query, "format": "rss"},
                )
                if r.status_code == 200 and "<item>" in r.text:
                    results = _bing_rss_parse(r.text, limit)
            except Exception:
                results = []
            if results:
                return {"data": {"web": results}}
        return {"data": {"web": results}}

    async def web_extract(self, urls: list[str], char_limit: int = 5000) -> dict[str, Any]:
        import httpx

        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_BROWSER_HEADERS) as client:
            for u in urls[:3]:
                content = ""
                try:
                    r = await client.get(u)
                    if r.status_code == 200:
                        content = _html_to_text(r.text)
                except Exception:
                    content = ""
                # Blocked (403/429) or JS-only shell -> reader proxy fallback
                if len(content) < 200:
                    try:
                        rr = await client.get(f"https://r.jina.ai/{u}", timeout=20)
                        if rr.status_code == 200 and len(rr.text) > len(content):
                            content = rr.text
                    except Exception:
                        pass
                if not content:
                    content = f"[extract error: could not fetch {u} (blocked or unreachable)]"
                results.append({"title": u, "url": u, "content": content[:char_limit]})
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

