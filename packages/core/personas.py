"""AI Advisory Council — expert personas (Task 3).

A persona is a **system-prompt override** applied on top of the shared LLM
container. There is NO separate model per expert — the same ``container.llm``
answers, but with a different system prompt so the voice / expertise changes.

Three expert personas ship:

* ``hormozi``  — Alex Hormozi: business strategy, offers, pricing, growth.
* ``buffett``  — Warren Buffett: value investing, capital allocation, stocks.
* ``garyvee``  — Gary Vaynerchuk: marketing, branding, social/content + finance.

``select_persona(text)`` performs deterministic keyword auto-detection so the
Telegram layer (and any free-text entry point) can route a question to the
right expert without an LLM call.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Persona system prompts
# ---------------------------------------------------------------------------
PERSONAS: dict[str, str] = {
    "hormozi": (
        "You are Alex Hormozi, a world-class business strategist focused on "
        "building valuable offers, pricing, and scalable growth. "
        "Answer in a direct, no-fluff, concrete style. Lead with the offer/"
        "value equation: more perceived value, less perceived cost/risk. "
        "Use punchy frameworks (Hooks, Star, Story, Solution; the Value "
        "Equation). Give actionable, specific advice and avoid vague "
        "generalities. When relevant, quantify the impact. Respond in the "
        "same language the user writes in (Vietnamese if they write Vietnamese)."
    ),
    "buffett": (
        "You are Warren Buffett, a disciplined value investor. You think in "
        "decades, not quarters. Emphasize moats, owner earnings, margin of "
        "safety, circle of competence, and opportunity cost. Be skeptical of "
        "hype and leverage; favor businesses you can understand. Explain "
        "investing principles plainly, with memorable analogies. Never "
        "recommend a specific ticker as a sure thing — teach the principles "
        "and the long-term mindset. Respond in the same language the user "
        "writes in (Vietnamese if they write Vietnamese)."
    ),
    "garyvee": (
        "You are Gary Vaynerchuk (GaryVee): relentless, energetic marketing "
        "and branding operator. You live for attention, narrative, social/"
        "content, and self-awareness. Push practical, hustle-driven, "
        "platform-specific marketing and personal-finance discipline. Be "
        "blunt, motivating, and concrete about content, community, and "
        "patience. Call out excuses. Respond in the same language the user "
        "writes in (Vietnamese if they write Vietnamese)."
    ),
}

# Human-readable labels for UI / Telegram rendering.
PERSONA_LABELS: dict[str, str] = {
    "hormozi": "Alex Hormozi (Chiến lược)",
    "buffett": "Warren Buffett (Đầu tư)",
    "garyvee": "Gary Vee (Marketing/Tài chính)",
}

# Keyword -> persona. Checked in dict order; first match wins.
# Keyword sets are intentionally disjoint to avoid ambiguous routing:
#   hormozi  -> strategy / offers / pricing / growth
#   buffett  -> investing / stocks / value investing
#   garyvee  -> marketing / branding / content / social + personal finance
PERSONA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hormozi": (
        "chiến lược",
        "strategy",
        "strategic",
        "tăng trưởng",
        "growth",
        "offer",
        "đề xuất",
        "định giá",
        "pricing",
        "bán hàng",
        "business model",
    ),
    "buffett": (
        "buffett",
        "warren",
        "đầu tư",
        "invest",
        "investing",
        "cổ phiếu",
        "stock",
        "stocks",
        "chứng khoán",
        "value investing",
        "cổ tức",
        "dividend",
    ),
    "garyvee": (
        "garyvee",
        "gary vee",
        "marketing",
        "tiếp thị",
        "brand",
        "thương hiệu",
        "social media",
        "content",
        "tài chính",
        "finance",
        "personal finance",
        "tiktok",
        "youtube channel",
    ),
}


def select_persona(text: str) -> str | None:
    """Deterministically detect an expert persona from free text.

    Returns one of ``"hormozi"``, ``"buffett"``, ``"garyvee"`` when a keyword
    matches, else ``None``. Matching is case-insensitive and substring-based.

    Order matters: ``hormozi`` is checked first, then ``buffett``, then
    ``garyvee`` (see :data:`PERSONA_KEYWORDS` for the disjoint keyword sets).
    """
    if not text:
        return None
    lowered = text.lower()
    for persona, keywords in PERSONA_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return persona
    return None


def available_personas() -> list[str]:
    """Return the list of registered persona keys (stable order)."""
    return list(PERSONAS.keys())


__all__ = [
    "PERSONAS",
    "PERSONA_LABELS",
    "PERSONA_KEYWORDS",
    "select_persona",
    "available_personas",
]
