"""Adversarial tests for the AI Advisory Council persona router (Phase 6).

select_persona() is deterministic keyword routing used by the Telegram layer
and any free-text entry point. These tests lock in correct routing and prevent
substring collisions from silently stealing traffic.
"""

from __future__ import annotations

import pytest

from packages.core.personas import (
    PERSONAS,
    PERSONA_KEYWORDS,
    available_personas,
    select_persona,
)


def test_empty_text_returns_none():
    assert select_persona("") is None
    assert select_persona("   ") is None


def test_explicit_buffett_keyword_wins():
    assert select_persona("buffett nói gì về cổ phiếu") == "buffett"
    assert select_persona("warren buffett value investing") == "buffett"


def test_stock_question_routes_to_buffett_not_hormozi():
    # Regression: 'giá' used to hijack this to hormozi.
    assert select_persona("giá cổ phiếu apple có nên mua không") == "buffett"
    assert select_persona("cổ phiếu VNM đang lên") == "buffett"


def test_gold_price_not_routed_to_any_expert():
    # 'giá vàng' is not a business-strategy/investing/persona question.
    assert select_persona("giá vàng hôm nay bao nhiêu") is None


def test_marketing_routes_to_garyvee():
    assert select_persona("marketing cho sản phẩm mới") == "garyvee"
    assert select_persona("làm content tiktok sao cho viral") == "garyvee"


def test_business_strategy_routes_to_hormozi():
    assert select_persona("chiến lược tăng trưởng cho startup") == "hormozi"
    assert select_persona("định giá gói subscription") == "hormozi"


def test_garyvee_finance_not_buffett():
    # personal finance is garyvee, not value investing
    assert select_persona("tài chính cá nhân nên chi tiêu thế nào") == "garyvee"


def test_substring_false_positive_brand():
    # 'brand' must not match 'brandnew' style noise; 'brand' alone is marketing.
    assert select_persona("xây dựng brand cho công ty") == "garyvee"


def test_first_match_wins_order():
    # A text containing both a hormozi and buffett keyword -> hormozi checked first.
    assert select_persona("chiến lược đầu tư cổ phiếu") == "hormozi"


def test_no_overlap_between_keyword_sets():
    """Keyword sets must stay disjoint so routing is unambiguous."""
    seen: dict[str, str] = {}
    for persona, kws in PERSONA_KEYWORDS.items():
        for kw in kws:
            if kw in seen:
                raise AssertionError(
                    f"keyword {kw!r} shared by {seen[kw]!r} and {persona!r}"
                )
            seen[kw] = persona


def test_all_personas_registered():
    for key in PERSONA_KEYWORDS:
        assert key in PERSONAS
    assert set(available_personas()) == set(PERSONAS.keys())
