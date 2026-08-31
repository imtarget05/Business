"""Tests: Telegram bold formatting that preserves Vietnamese + emoji.

Covers the user request to send the Michelin food list to Telegram with dish
names in BOLD — using HTML mode (<b>...</b>) so Telegram never prints a literal
'*' or garbled chars ("lỗi phông"). Falls back to MarkdownV2 when requested.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FOODS = ROOT / "tests" / "fixtures" / "michelin_foods_vietnam.txt"

from packages.core.text_format import bold, format_michelin_bold


def test_format_michelin_bold_html_wraps_dish_names():
    raw = "1. 🍜 Bún Cà Ri (Bún Riêu) - Cà ri tôm ở Hà Nội\n2. 🍲 Phở - Phở bò ở Sài Gòn\n"
    out = format_michelin_bold(raw, mode="html")
    # Dish name wrapped in <b>...</b>, emoji stays OUTSIDE the tag.
    assert "<b>Bún Cà Ri (Bún Riêu)</b>" in out
    assert "<b>Phở</b>" in out
    # No literal asterisks leaked (the old broken behaviour).
    assert "*" not in out
    # Separator + remainder preserved.
    assert " - Cà ri tôm ở Hà Nội" in out
    assert " - Phở bò ở Sài Gòn" in out
    # Emoji preserved.
    assert "🍜" in out and "🍲" in out


def test_format_michelin_bold_keeps_diacritics_after_emoji():
    raw = "3. 🥞 Bánh Xèo - Bánh xèo tôm ở Hà Nội"
    out = format_michelin_bold(raw, mode="html")
    assert "<b>Bánh Xèo</b>" in out
    # Diacritic intact (not collapsed to 'Ban Xeo').
    assert "Bánh Xèo" in out
    assert "🥞" in out


def test_format_michelin_bold_normalizes_nfd_input():
    """Phone copy-paste that decomposes Vietnamese (NFD) must still bold + compose."""
    nfd = "3. \U0001fad9 B\u00e1nh X\u00e8o - test"
    out = format_michelin_bold(nfd, mode="html")
    assert "<b>Bánh Xèo</b>" in out
    assert "ð" not in out


def test_bold_helper_html_nfc():
    assert bold("Cơm Tấm") == "<b>Cơm Tấm</b>"
    assert "Cơm Tấm" in bold("Cơm Tấm")
    assert "*" not in bold("Cơm Tấm")


def test_michelin_foods_fixture_roundtrip_and_bold():
    """Full 8-item fixture decodes cleanly, keeps all dishes + emoji, and bolds
    correctly for Telegram HTML mode (no literal '*' leaked)."""
    raw = FOODS.read_bytes().decode("utf-8")
    assert "ð" not in raw
    for dish in (
        "Bún Cà Ri",
        "Phở",
        "Bánh Xèo",
        "Bún Thang",
        "Cơm Tấm",
        "Bánh Mì",
        "Gỏi Cuốn",
        "Bún Riêu",
    ):
        assert dish in raw, dish
    for emo in ("🍜", "🍲", "🥞", "🍚", "🥖", "🌿"):
        assert emo in raw, emo
    out = format_michelin_bold(raw, mode="html")
    assert "<b>Bún Cà Ri (Bún Riêu)</b>" in out
    assert "<b>Phở</b>" in out
    assert "<b>Bún Riêu</b>" in out
    assert "*" not in out  # never leak literal asterisk
    assert raw.count("\n") + 1 >= 8


def test_format_michelin_bold_markdownv2_mode():
    raw = "1. 🍜 Bún Cà Ri - test"
    out = format_michelin_bold(raw, mode="mdv2")
    assert "*Bún Cà Ri*" in out
