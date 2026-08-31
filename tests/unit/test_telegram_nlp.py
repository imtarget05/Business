"""Unit tests for the dependency-free Vietnamese NLP helpers (Feature 5 — UX).

Covers query normalization (lowercase / diacritics / whitespace) and intent
classification for the phrasings Vietnamese business users actually type —
with and without dấu — plus the false positives that must NOT match.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from packages.telegram.nlp import (
    CAP_HEALTH,
    CAP_KNOWLEDGE,
    CAP_REPORTING,
    CAP_SUPPORT,
    INTENT_KEYWORDS,
    classify_vietnamese_intent,
    normalize_vietnamese_query,
    strip_diacritics,
)

# ---------------------------------------------------------------------------
# normalize_vietnamese_query / strip_diacritics
# ---------------------------------------------------------------------------


def test_normalize_lowercases_and_collapses_whitespace():
    assert normalize_vietnamese_query("  Tìm   QUÁN  ăn \n\t ngon  ") == "tìm quán ăn ngon"


def test_normalize_keeps_diacritics_by_default():
    assert normalize_vietnamese_query("Báo Cáo Doanh Thu") == "báo cáo doanh thu"


def test_normalize_can_strip_diacritics():
    assert normalize_vietnamese_query("Tìm quán ăn", strip_accents=True) == "tim quan an"
    assert (
        normalize_vietnamese_query("ĐÁNH GIÁ NHÀ HÀNG", strip_accents=True) == "danh gia nha hang"
    )


def test_normalize_handles_empty_input():
    assert normalize_vietnamese_query("") == ""
    assert normalize_vietnamese_query(None) == ""  # type: ignore[arg-type]
    assert normalize_vietnamese_query("   \n  ") == ""


def test_strip_diacritics_covers_d_stroke():
    assert strip_diacritics("Đơn hàng đã đến") == "Don hang da den"
    assert strip_diacritics("sức khỏe hệ thống") == "suc khoe he thong"


def test_normalize_is_idempotent():
    once = normalize_vietnamese_query("  Hỗ  TRỢ  ", strip_accents=True)
    assert normalize_vietnamese_query(once, strip_accents=True) == once == "ho tro"


# ---------------------------------------------------------------------------
# classify_vietnamese_intent — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "tìm quán ăn",
        "tìm quán ăn ngon ở Hà Nội",
        "gợi ý món ngon",
        "gợi ý món ngon cho bữa trưa",
        "đánh giá nhà hàng",
        "đánh giá nhà hàng nào tốt nhất Sài Gòn",
        "quán nào ngon ở Đà Nẵng?",
        "trưa nay ăn gì",
        "nhà hàng đạt sao Michelin 2026",
        "cho mình xem chính sách hoàn tiền",
    ],
)
def test_knowledge_query_intent(text: str):
    assert classify_vietnamese_intent(text) == CAP_KNOWLEDGE


@pytest.mark.parametrize(
    "text",
    [
        "báo cáo",
        "cho tôi xem báo cáo hôm nay",
        "tình hình",
        "tình hình kinh doanh tuần này thế nào",
        "doanh thu tháng 8",
        "thống kê số liệu quý 3",
        "tiến độ dự án ra sao",
    ],
)
def test_reporting_intent(text: str):
    assert classify_vietnamese_intent(text) == CAP_REPORTING


@pytest.mark.parametrize(
    "text",
    [
        "hỗ trợ",
        "tôi cần hỗ trợ",
        "khiếu nại",
        "tôi muốn khiếu nại về đơn hàng",
        "cho mình trả hàng",
        "app bị lỗi rồi",
        "hệ thống không hoạt động",
        "máy in hỏng",
        "muốn phản ánh chất lượng dịch vụ",
    ],
)
def test_support_triage_intent(text: str):
    assert classify_vietnamese_intent(text) == CAP_SUPPORT


@pytest.mark.parametrize(
    "text",
    [
        "sức khỏe hệ thống",
        "sức khỏe hệ thống thế nào",
        "kiểm tra hệ thống giúp mình",
        "tình trạng hệ thống hiện tại",
        "health check",
    ],
)
def test_health_intent(text: str):
    assert classify_vietnamese_intent(text) == CAP_HEALTH


# ---------------------------------------------------------------------------
# classify_vietnamese_intent — robustness
# ---------------------------------------------------------------------------


def test_classification_is_accent_insensitive():
    assert classify_vietnamese_intent("tim quan an gan day") == CAP_KNOWLEDGE
    assert classify_vietnamese_intent("bao cao tinh hinh kinh doanh") == CAP_REPORTING
    assert classify_vietnamese_intent("toi can ho tro") == CAP_SUPPORT
    assert classify_vietnamese_intent("kiem tra he thong") == CAP_HEALTH


def test_classification_is_case_insensitive():
    assert classify_vietnamese_intent("TÌM QUÁN ĂN") == CAP_KNOWLEDGE
    assert classify_vietnamese_intent("Báo Cáo") == CAP_REPORTING


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "trời hôm nay đẹp quá",
        "1 + 1 bằng mấy",
        "viết code python hello world",
        "tìm việc làm AI intern tại Hà Nội",
        "gửi lời chào tới friend@company.com",
        "Nghề nào đang bị layoff nhiều nhất 2026?",
    ],
)
def test_unknown_returns_none(text: str):
    """Unknown phrasing must return None so the caller can fall back to /help."""
    assert classify_vietnamese_intent(text) is None


def test_none_input_is_safe():
    assert classify_vietnamese_intent(None) is None  # type: ignore[arg-type]


def test_longest_phrase_wins_over_generic_one():
    """A long phrase like "kiểm tra hệ thống" beats shorter, generic matches."""
    assert classify_vietnamese_intent("kiểm tra hệ thống có ổn không") == CAP_HEALTH
    # "đánh giá nhà hàng" (17) beats "nhà hàng" (8) — both knowledge, still stable.
    assert classify_vietnamese_intent("đánh giá nhà hàng") == CAP_KNOWLEDGE


def test_word_boundaries_prevent_false_positives():
    """Keywords must not match inside a longer token once accents are stripped."""
    # "lời chào" -> "loi chao": must not be read as a "lỗi" support ticket.
    assert classify_vietnamese_intent("gửi lời chào tới đối tác") is None
    # "báo giá" -> "bao gia" must not be confused with "báo cáo" -> "bao cao".
    assert classify_vietnamese_intent("gửi báo giá cho khách") != CAP_REPORTING


def test_keyword_dictionary_shape():
    """The dict stays dependency-free data: capability -> tuple of phrases."""
    assert set(INTENT_KEYWORDS) == {CAP_KNOWLEDGE, CAP_REPORTING, CAP_SUPPORT, CAP_HEALTH}
    for capability, keywords in INTENT_KEYWORDS.items():
        assert isinstance(keywords, tuple) and keywords, capability
        assert all(isinstance(k, str) and k.strip() for k in keywords), capability


def test_every_keyword_classifies_to_its_own_capability():
    """Sanity net: no keyword may be shadowed by another capability's phrase."""
    for capability, keywords in INTENT_KEYWORDS.items():
        for keyword in keywords:
            assert classify_vietnamese_intent(keyword) == capability, keyword
