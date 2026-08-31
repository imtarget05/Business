# -*- coding: utf-8 -*-
"""Dependency-free Vietnamese intent normalization for the Telegram bot.

Why pure Python (no underthesea / spaCy / LLM call):
- the bot must import and the unit tests must run fully OFFLINE,
- intent detection on the hot path must cost 0đ and ~0ms (an LLM call for
  "báo cáo" is pure waste),
- deterministic rules are auditable — the same phrasing always routes the same
  way, which is what Vietnamese business users expect.

Public API:
    normalize_vietnamese_query(text, strip_accents=False) -> str
    strip_diacritics(text) -> str
    classify_vietnamese_intent(text) -> str | None   # capability, e.g. "reporting"

The capability strings mirror `packages.core.router` naming ("knowledge.query",
"support.triage") plus the two monitoring-only routes the bot owns.
"""

from __future__ import annotations

import re
import unicodedata

from packages.core.router import VIETNAMESE_INTENT_KEYWORDS

# Capabilities returned by classify_vietnamese_intent.
CAP_KNOWLEDGE = "knowledge.query"
CAP_REPORTING = "reporting"
CAP_SUPPORT = "support.triage"
CAP_HEALTH = "monitoring.health"

_WS_RE = re.compile(r"\s+")
# "đ"/"Đ" are NOT decomposed by NFD, so they need an explicit mapping.
_D_TRANS = str.maketrans({"đ": "d", "Đ": "D"})


def strip_diacritics(text: str) -> str:
    """Remove Vietnamese diacritics ("tìm quán ăn" -> "tim quan an").

    Users type both with and without dấu (and Telegram keyboards on desktop
    often drop them), so matching happens on the accent-free form.
    """
    if not text:
        return text
    decomposed = unicodedata.normalize("NFD", text.translate(_D_TRANS))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped)


def normalize_vietnamese_query(text: str, *, strip_accents: bool = False) -> str:
    """Lowercase, NFC-normalize and collapse whitespace of a user query.

    Args:
        text: raw Telegram message text (may be None-ish / multiline).
        strip_accents: also drop diacritics — used for keyword matching, NOT for
            anything shown back to the user (Vietnamese without dấu reads badly).

    Returns:
        A single-line, single-spaced, lowercase string ("" for empty input).
    """
    if not text:
        return ""
    out = unicodedata.normalize("NFC", str(text)).strip().lower()
    out = _WS_RE.sub(" ", out)
    if strip_accents:
        out = strip_diacritics(out)
    return out


# ---------------------------------------------------------------------------
# Intent keyword dictionary (written with dấu for readability; normalized to the
# accent-free form at import time). Keep phrases SPECIFIC — a bare word like
# "lỗi" would swallow "gửi lời chào" once accents are stripped.
# ---------------------------------------------------------------------------

INTENT_KEYWORDS = VIETNAMESE_INTENT_KEYWORDS

# Tie-break order when two capabilities match keywords of the same length.
# Health first: "kiểm tra hệ thống" must never be read as a business report.
INTENT_PRIORITY: tuple[str, ...] = (CAP_HEALTH, CAP_SUPPORT, CAP_REPORTING, CAP_KNOWLEDGE)

# Friendly Vietnamese labels (used by the bot when it echoes what it understood).
CAPABILITY_LABELS: dict[str, str] = {
    CAP_KNOWLEDGE: "🔍 Tra cứu / tìm món ăn",
    CAP_REPORTING: "📊 Báo cáo",
    CAP_SUPPORT: "❓ Hỗ trợ",
    CAP_HEALTH: "🩺 Sức khỏe hệ thống",
}


def _compile_rules() -> tuple[tuple[str, re.Pattern[str], int], ...]:
    """Pre-compile (capability, pattern, weight) rules, longest phrase first."""
    rules: list[tuple[str, re.Pattern[str], int]] = []
    for capability, keywords in INTENT_KEYWORDS.items():
        for keyword in keywords:
            norm = normalize_vietnamese_query(keyword, strip_accents=True)
            if not norm:
                continue
            # Word-ish boundaries: "hỏng" must not match inside another token.
            pattern = re.compile(
                r"(?<![0-9a-z])" + re.escape(norm) + r"(?![0-9a-z])"
            )
            rules.append((capability, pattern, len(norm)))
    rules.sort(key=lambda item: item[2], reverse=True)
    return tuple(rules)


_RULES = _compile_rules()


def classify_vietnamese_intent(text: str) -> str | None:
    """Map common Vietnamese phrasings to a capability string.

    Longest matching phrase wins ("đánh giá nhà hàng" beats "nhà hàng"), ties are
    broken by INTENT_PRIORITY. Returns None when nothing matches so the caller
    can fall back to /help instead of guessing.

    Examples:
        "tìm quán ăn ngon ở Hà Nội" -> "knowledge.query"
        "cho tôi xem báo cáo hôm nay" -> "reporting"
        "tôi muốn khiếu nại đơn hàng" -> "support.triage"
        "trời hôm nay đẹp quá" -> None
    """
    normalized = normalize_vietnamese_query(text, strip_accents=True)
    if not normalized:
        return None

    best_capability: str | None = None
    best_weight = 0
    for capability, pattern, weight in _RULES:
        if weight < best_weight:
            break  # rules are length-sorted: nothing longer can follow
        if not pattern.search(normalized):
            continue
        if weight > best_weight:
            best_capability, best_weight = capability, weight
        elif best_capability is not None and capability != best_capability:
            # Same-length match on a different capability -> priority decides.
            order = INTENT_PRIORITY
            current = order.index(best_capability) if best_capability in order else len(order)
            challenger = order.index(capability) if capability in order else len(order)
            if challenger < current:
                best_capability = capability
    return best_capability


__all__ = [
    "CAPABILITY_LABELS",
    "CAP_HEALTH",
    "CAP_KNOWLEDGE",
    "CAP_REPORTING",
    "CAP_SUPPORT",
    "INTENT_KEYWORDS",
    "INTENT_PRIORITY",
    "classify_vietnamese_intent",
    "normalize_vietnamese_query",
    "strip_diacritics",
]