"""Competitive Intelligence Agent (Task 5).

Implements the TikTok-infographic flow:

    COLLECT competitor posts/pricing  ->  ANALYZE patterns/shifts  ->  WEEKLY BRIEF

* ``collect`` pulls competitor signals via ``packages/tools/web.py`` ``web_search``
  (NO LLM self-crawl — only search + parse title/url/snippet/date).
* ``analyze`` groups signals by competitor, detects pricing shifts and posting
  patterns using a deterministic heuristic, then asks the shared LLM for a
  *light* Vietnamese summary. If the LLM is unavailable it falls back to the
  heuristic summary (never fabricates, never raises).
* ``weekly_brief`` synthesizes the analysis into a short Markdown brief
  (<400 words, Vietnamese): top movers, pricing shifts, recommended moves.

Capabilities: ``competitor.brief`` (+ ``competitor.collect``).
Domain: ``competitor`` (``Domain.COMPETITOR``).

Design for testability
----------------------
The full pipeline (collect -> parse -> analyze -> brief) is deterministic given
the injected ``web_tools`` provider, so the unit test mocks ``web_search`` and
asserts parsed signals + brief structure with **no network**. The LLM is
optional: ``analyze`` and ``weekly_brief`` work offline with the heuristic
fallback when ``llm`` is ``None`` or raises.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskRequest,
)
from packages.llm.base import LLMProvider
from packages.tools.web import WebToolsProvider, create_web_tools

# --------------------------------------------------------------------------- #
# Paths (resolved relative to this file so tests run from repo root)
# --------------------------------------------------------------------------- #
_AGENTS_COMP_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AGENTS_COMP_DIR.parent.parent
DATA_DIR = _REPO_ROOT / "data"
COMPETITORS_CONFIG_PATH = DATA_DIR / "competitor" / "competitors.json"

# Pricing language cues (VN + EN) used by the heuristic analyzer.
_PRICE_UP_KEYWORDS = ("tăng giá", "tang gia", "increase", "premium", "đắt hơn", "dat hon")
_PRICE_DOWN_KEYWORDS = (
    "giảm giá",
    "giam gia",
    "discount",
    "sale",
    "khuyến mãi",
    "khuyen mai",
    "giảm",
    "giam",
    "reduce",
    "rẻ hơn",
    "re hon",
    "flash sale",
    "ưu đãi",
    "uu dai",
)
_PATTERN_KEYWORDS = (
    "ra mắt",
    "ra mat",
    "launch",
    "sản phẩm mới",
    "san pham moi",
    "tính năng",
    "tinh nang",
    "feature",
    "mở rộng",
    "mo rong",
    "expand",
    "đối tác",
    "doi tac",
    "partner",
    "campaign",
    "chiến dịch",
    "chien dich",
)

# Price regex: 2 triệu / 1.200.000 / 1,200,000 / 1200000 / 250k / 19$ / 1.2tr
# Order matters: the "triệu/tr" variant must be tried BEFORE the bare-digit
# variant, otherwise "2" wins and "triệu" is dropped.
_PRICE_RE = re.compile(
    r"(?P<amount>\d+\s?(?:triệu|tr|TR|TRIỆU)|\d[\d\.,]*(?:\s?[kK])?)"
    r"(?P<unit>(?:\s?(?:VND|đ|vnđ|USD|\$))?)",
    re.IGNORECASE,
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_competitors(path: Path | None = None) -> dict[str, Any]:
    """Load tracked-competitor config (names + keywords + queries).

    Returns the parsed JSON. Falls back to an empty config dict if the file is
    missing so the agent still works (just with no named competitors).
    """
    p = path or COMPETITORS_CONFIG_PATH
    if not p.exists():
        return {"competitors": [], "queries": []}
    return _load_json(p)


# --------------------------------------------------------------------------- #
# Result models
# --------------------------------------------------------------------------- #
class CompetitorSignal(BaseModel):
    """One collected competitive signal (a search hit about a competitor)."""

    id: str
    competitor: str  # resolved competitor name (or "unknown")
    source_url: str
    title: str
    snippet: str
    query: str
    raw_date: str | None = None
    collected_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    price_amount: float | None = None
    price_unit: str | None = None
    price_direction: str | None = None  # "up" | "down" | None (from text cues)
    pattern_tags: list[str] = Field(default_factory=list)


class CompetitorAnalysis(BaseModel):
    """Heuristic + (optional) LLM analysis of a signal set."""

    by_competitor: dict[str, int] = Field(default_factory=dict)
    total_signals: int = 0
    pricing_signals: int = 0
    price_shifts: list[dict[str, Any]] = Field(default_factory=list)
    pattern_tags: list[str] = Field(default_factory=list)
    summary: str = ""  # light LLM/VN summary (heuristic fallback if no LLM)
    heuristics_only: bool = True


# --------------------------------------------------------------------------- #
# Parsing helpers (deterministic)
# --------------------------------------------------------------------------- #
def _detect_price(text: str) -> tuple[float | None, str | None]:
    """Best-effort price extraction from a snippet/title (VN-aware)."""
    if not text:
        return None, None
    m = _PRICE_RE.search(text)
    if not m:
        return None, None
    raw = m.group("amount")
    unit = (m.group("unit") or "").strip().upper() or None
    try:
        low = raw.lower()
        if "triệu" in low or " tr" in f" {low} " or low.endswith("tr"):
            digits = re.sub(r"[^\d]", "", low.replace("triệu", "").replace("tr", ""))
            amount = float(digits) * 1_000_000 if digits else None
        else:
            digits = re.sub(r"[^\d]", "", raw)
            amount = float(digits) if digits else None
            if raw.lower().endswith("k"):
                amount = (amount or 0) * 1_000
    except (ValueError, TypeError):
        return None, None
    if amount is None:
        return None, None
    if unit is None:
        unit = "VND"  # default assumption for VN context
    return amount, unit


def _direction_for(text: str) -> str | None:
    low = text.lower()
    if any(k in low for k in _PRICE_UP_KEYWORDS):
        return "up"
    if any(k in low for k in _PRICE_DOWN_KEYWORDS):
        return "down"
    return None


def _pattern_tags_for(text: str) -> list[str]:
    low = text.lower()
    tags = [kw for kw in _PATTERN_KEYWORDS if kw in low]
    return tags


def _resolve_competitor(url: str, title: str, snippet: str, known: list[dict[str, Any]]) -> str:
    """Resolve a signal to a tracked competitor name by alias/keyword match.

    Falls back to the URL host (e.g. ``example.com``) when no alias matches.
    """
    haystack = f"{title} {snippet} {url}".lower()
    for c in known:
        name = c.get("name", "")
        aliases = [name, *c.get("aliases", []), *c.get("keywords", [])]
        for alias in aliases:
            if alias and str(alias).lower() in haystack:
                return name
    # Fallback: strip to host.
    try:
        host = url.split("/")[2].lower() if len(url.split("/")) > 2 else url
        return host or "unknown"
    except Exception:
        return "unknown"


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
class CompetitorAgent:
    """Competitive Intelligence: COLLECT -> ANALYZE -> WEEKLY BRIEF."""

    def __init__(
        self,
        *,
        llm: LLMProvider | None = None,
        web_tools: WebToolsProvider | None = None,
        descriptor: AgentDescriptor | None = None,
        competitors_path: Path | None = None,
    ) -> None:
        self._llm = llm
        self._web = web_tools or create_web_tools("auto")
        self._competitors_path = competitors_path or COMPETITORS_CONFIG_PATH
        self.descriptor = descriptor or AgentDescriptor(
            name="competitor",
            domain=Domain.COMPETITOR,
            version="1",
            description=(
                "Competitive Intelligence: collect competitor posts/pricing via "
                "web_search (no LLM crawl), analyze patterns and pricing shifts, "
                "and emit a short Vietnamese weekly brief (competitor.brief, "
                "competitor.collect)."
            ),
            capabilities=frozenset({"competitor.brief", "competitor.collect"}),
        )

    # ------------------------------------------------------------------ #
    # COLLECT
    # ------------------------------------------------------------------ #
    async def collect(self, queries: list[str], limit: int = 5) -> list[CompetitorSignal]:
        """Collect competitor signals for the given search queries.

        Uses the injected ``web_tools`` provider (``web_search``) — never an LLM
        crawl. Each result is parsed into a :class:`CompetitorSignal`.
        """
        cfg = load_competitors(self._competitors_path)
        known = cfg.get("competitors", []) or []
        signals: list[CompetitorSignal] = []
        for q in queries:
            if not q or not q.strip():
                continue
            try:
                raw = await self._web.web_search(q.strip(), limit=limit)
            except Exception:
                continue  # skip a failing query, never fabricate
            results = (raw or {}).get("data", {}).get("web", []) or []
            for r in results:
                url = str(r.get("url", "") or "")
                title = str(r.get("title", "") or "")
                snippet = str(r.get("snippet", "") or "")
                competitor = _resolve_competitor(url, title, snippet, known)
                amount, unit = _detect_price(f"{title} {snippet}")
                direction = _direction_for(f"{title} {snippet}")
                tags = _pattern_tags_for(f"{title} {snippet}")
                signals.append(
                    CompetitorSignal(
                        id=f"{abs(hash(q + url + title)) % 10**12:012d}",
                        competitor=competitor,
                        source_url=url,
                        title=title,
                        snippet=snippet,
                        query=q.strip(),
                        raw_date=r.get("date"),  # most providers omit this; None is fine
                        price_amount=amount,
                        price_unit=unit,
                        price_direction=direction,
                        pattern_tags=tags,
                    )
                )
        return signals

    # ------------------------------------------------------------------ #
    # ANALYZE
    # ------------------------------------------------------------------ #
    async def analyze(self, signals: list[CompetitorSignal]) -> CompetitorAnalysis:
        """Group signals by competitor, detect pricing shifts + patterns.

        Heuristic-first; asks the shared LLM for a light VN summary when one is
        injected and succeeds. On any LLM failure, keep the heuristic summary.
        """
        by_competitor: dict[str, int] = {}
        pricing = 0
        price_shifts: list[dict[str, Any]] = []
        pattern_set: set[str] = set()
        for s in signals:
            by_competitor[s.competitor] = by_competitor.get(s.competitor, 0) + 1
            pattern_set.update(s.pattern_tags)
            if s.price_amount is not None:
                pricing += 1
            if s.price_direction:
                price_shifts.append(
                    {
                        "competitor": s.competitor,
                        "direction": s.price_direction,
                        "amount": s.price_amount,
                        "unit": s.price_unit,
                        "title": s.title,
                        "url": s.source_url,
                    }
                )

        analysis = CompetitorAnalysis(
            by_competitor=by_competitor,
            total_signals=len(signals),
            pricing_signals=pricing,
            price_shifts=price_shifts,
            pattern_tags=sorted(pattern_set),
            heuristics_only=True,
        )
        analysis.summary = self._heuristic_summary(analysis)

        # Light LLM summary (optional, non-fatal).
        if self._llm is not None and signals:
            try:
                prompt = self._build_analysis_prompt(analysis, signals)
                text = await self._llm.generate(
                    prompt=prompt,
                    system=(
                        "Bạn là chuyên gia tình báo cạnh tranh. Tóm tắt ngắn gọn "
                        "(<=120 từ, tiếng Việt) những dịch chuyển chính của đối thủ "
                        "từ dữ liệu đã cho. Không bịa. Nếu ít dữ liệu, nói rõ."
                    ),
                    temperature=0.2,
                    max_tokens=300,
                )
                if text and text.strip():
                    analysis.summary = text.strip()
                    analysis.heuristics_only = False
            except Exception:
                # Keep heuristic summary; never raise.
                pass
        return analysis

    @staticmethod
    def _heuristic_summary(a: CompetitorAnalysis) -> str:
        if a.total_signals == 0:
            return "Chưa thu thập được tín hiệu đối thủ nào trong kỳ này."
        top = sorted(a.by_competitor.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines = [f"Phát hiện {a.total_signals} tín hiệu từ {len(a.by_competitor)} đối thủ."]
        lines.append("Nhiều nhất: " + ", ".join(f"{n} ({c})" for n, c in top) + ".")
        if a.pricing_signals:
            lines.append(f"Có {a.pricing_signals} tín hiệu liên quan đến giá.")
        if a.price_shifts:
            ups = [p for p in a.price_shifts if p["direction"] == "up"]
            downs = [p for p in a.price_shifts if p["direction"] == "down"]
            if ups:
                lines.append(f"Xu hướng tăng giá: {len(ups)} tín hiệu.")
            if downs:
                lines.append(f"Xu hướng giảm/giảm giá: {len(downs)} tín hiệu.")
        if a.pattern_tags:
            lines.append("Chủ đề nổi bật: " + ", ".join(a.pattern_tags[:6]) + ".")
        return " ".join(lines)

    @staticmethod
    def _build_analysis_prompt(a: CompetitorAnalysis, signals: list[CompetitorSignal]) -> str:
        top = sorted(a.by_competitor.items(), key=lambda kv: kv[1], reverse=True)[:5]
        parts = ["Tín hiệu theo đối thủ:"]
        for n, c in top:
            parts.append(f"- {n}: {c} tín hiệu")
        if a.price_shifts:
            parts.append("Dịch chuyển giá:")
            for p in a.price_shifts[:8]:
                amt = f"{p['amount']:,.0f} {p['unit']}" if p.get("amount") else ""
                parts.append(f"- {p['competitor']} ({p['direction']}) {amt}: {p['title']}")
        if a.pattern_tags:
            parts.append("Chủ đề: " + ", ".join(a.pattern_tags[:8]))
        parts.append("\nTóm tắt thành 1 đoạn ngắn tiếng Việt.")
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    # WEEKLY BRIEF
    # ------------------------------------------------------------------ #
    async def weekly_brief(
        self,
        org_id: str | None = None,
        *,
        competitor: str | None = None,
        limit: int = 5,
    ) -> str:
        """Collect (from config) -> analyze -> short VN Markdown brief (<400 words).

        ``competitor`` optionally narrows the brief to one tracked rival.
        """
        cfg = load_competitors(self._competitors_path)
        queries = list(cfg.get("queries", []) or [])
        if competitor:
            # Build targeted queries from the competitor's keywords/aliases.
            comp = None
            for c in cfg.get("competitors", []) or []:
                if c.get("name") == competitor:
                    comp = c
                    break
            if comp:
                base = [comp.get("name", ""), *comp.get("aliases", []), *comp.get("keywords", [])]
                base = [b for b in base if b]
                queries = [f"{b} giá" for b in base[:3]] or queries
        if not queries:
            queries = ["đối thủ cạnh tranh giá chiến lược"]

        signals = await self.collect(queries, limit=limit)
        if competitor:
            signals = [s for s in signals if s.competitor == competitor] or signals
        analysis = await self.analyze(signals)
        return self._render_brief(analysis, signals, competitor=competitor)

    @staticmethod
    def _render_brief(
        a: CompetitorAnalysis,
        signals: list[CompetitorSignal],
        *,
        competitor: str | None = None,
    ) -> str:
        """Render the brief Markdown. Kept under ~400 words (Vietnamese)."""
        week = datetime.now(UTC).strftime("%d/%m/%Y")
        title = "📊 Weekly Competitive Brief" + (f" — {competitor}" if competitor else "")
        lines = [f"*{title}*  ({week})", ""]

        if a.total_signals == 0:
            lines.append("_Chưa có tín hiệu đối thủ nào trong tuần này._")
            return "\n".join(lines)

        # Top movers
        lines.append("*🚀 Top movers:*")
        top = sorted(a.by_competitor.items(), key=lambda kv: kv[1], reverse=True)[:5]
        for n, c in top:
            lines.append(f"• {n}: {c} tín hiệu")
        lines.append("")

        # Pricing shifts
        lines.append("*💰 Dịch chuyển giá:*")
        if a.price_shifts:
            for p in a.price_shifts[:5]:
                amt = f" {p['amount']:,.0f} {p['unit']}" if p.get("amount") else ""
                arrow = "↑" if p["direction"] == "up" else "↓"
                lines.append(f"• {arrow} {p['competitor']}{amt} — {p['title']}")
        else:
            lines.append("• Chưa phát hiện dịch chuyển giá rõ rệt.")
        lines.append("")

        # Patterns
        if a.pattern_tags:
            lines.append("*🔎 Chủ đề nổi bật:* " + ", ".join(a.pattern_tags[:8]))
            lines.append("")

        # Summary
        lines.append("*🧭 Tóm tắt:*")
        summary = a.summary.strip()
        # Keep brief tight (<400 words).
        words = summary.split()
        if len(words) > 380:
            summary = " ".join(words[:380]) + " …"
        lines.append(summary)
        lines.append("")

        # Recommendations (heuristic)
        recs = CompetitorAgent._recommendations(a)
        if recs:
            lines.append("*✅ Đề xuất:*")
            for r in recs:
                lines.append(f"• {r}")

        return "\n".join(lines).strip()

    @staticmethod
    def _recommendations(a: CompetitorAnalysis) -> list[str]:
        recs: list[str] = []
        if a.price_shifts:
            downs = [p for p in a.price_shifts if p["direction"] == "down"]
            ups = [p for p in a.price_shifts if p["direction"] == "up"]
            if downs:
                recs.append(
                    "Đối thủ đang giảm giá — xem xét ưu đãi đi kèm (value-add) thay vì hạ giá."
                )
            if ups:
                recs.append(
                    "Đối thủ tăng giá — cơ hội nhấn mạnh giá trị/gói của ta "
                    "để giành khách nhạy cảm giá."
                )
        if "ra mắt" in a.pattern_tags or "launch" in a.pattern_tags:
            recs.append(
                "Đối thủ ra mắt tính năng/sản phẩm mới — rà soát USP và nội dung đối chiếu."
            )
        if not recs:
            recs.append("Duy trì giám sát; chưa có dịch chuyển đáng kể cần hành động ngay.")
        return recs

    # ------------------------------------------------------------------ #
    # Capability handler
    # ------------------------------------------------------------------ #
    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in ("brief", "collect"):
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=(
                        f"competitor only supports actions 'brief'/'collect', "
                        f"got {request.action!r}"
                    ),
                ),
            )

        try:
            if request.action == "collect":
                queries = request.payload.get("queries") or []
                if isinstance(queries, str):
                    queries = [queries]
                if not queries:
                    # Derive from competitor config when no explicit queries.
                    cfg = load_competitors(self._competitors_path)
                    queries = list(cfg.get("queries", []) or [])
                limit = int(request.payload.get("limit", 5))
                signals = await self.collect(list(queries), limit=limit)
                return AgentResponse(
                    task_id=request.task_id,
                    agent=self.descriptor.qualified_name,
                    status=AgentResponseStatus.SUCCESS,
                    result={
                        "signals": [s.model_dump() for s in signals],
                        "count": len(signals),
                    },
                    confidence=0.8,
                    metadata={"action": "collect"},
                )

            # brief
            org_id = request.payload.get("org_id") or (
                str(request.context.organization_id) if request.context.organization_id else None
            )
            competitor = request.payload.get("competitor")
            brief = await self.weekly_brief(
                org_id,
                competitor=competitor,
                limit=int(request.payload.get("limit", 5)),
            )
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"brief": brief, "competitor": competitor},
                confidence=0.8,
                metadata={"action": "brief"},
            )
        except Exception as e:  # surface, never fabricate
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="COMPETITOR_ERROR", message=str(e)),
            )


def create_competitor_agent(
    *,
    llm: LLMProvider | None = None,
    web_tools: WebToolsProvider | None = None,
    competitors_path: Path | None = None,
) -> CompetitorAgent:
    """Factory used by bootstrap / scripts (mirrors other agents)."""
    return CompetitorAgent(llm=llm, web_tools=web_tools, competitors_path=competitors_path)


__all__ = [
    "CompetitorAgent",
    "CompetitorSignal",
    "CompetitorAnalysis",
    "create_competitor_agent",
    "load_competitors",
    "COMPETITORS_CONFIG_PATH",
]
