"""Telegram UX layer (Feature 5): per-user sessions + Vietnamese intent rules.

Dependency-free on purpose — importing this package must never require
python-telegram-bot or any NLP library, so handlers and tests work offline.
"""

from packages.telegram.nlp import (
    CAPABILITY_LABELS,
    INTENT_KEYWORDS,
    classify_vietnamese_intent,
    normalize_vietnamese_query,
    strip_diacritics,
)
from packages.telegram.session import (
    DEFAULT_TTL_SECONDS,
    MAX_HISTORY,
    Session,
    SessionStore,
)

__all__ = [
    "CAPABILITY_LABELS",
    "DEFAULT_TTL_SECONDS",
    "INTENT_KEYWORDS",
    "MAX_HISTORY",
    "Session",
    "SessionStore",
    "classify_vietnamese_intent",
    "normalize_vietnamese_query",
    "strip_diacritics",
]
