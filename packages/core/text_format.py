# -*- coding: utf-8 -*-
"""Text formatting helpers for Telegram output.

Keeps Vietnamese diacritics + emoji intact while applying Telegram bold, using
the HTML mode (<b>...</b>) which is the most robust against "lỗi phông chữ" and
stray-asterisk rendering (the old MARKDOWN mode prints literal *...* when it
fails to parse). Emoji and diacritics are preserved.
"""
from __future__ import annotations

import re
import unicodedata

# A numbered food/restaurant line looks like: "1. 🍜 Bún Cà Ri (Bún Riêu) - ..."
# We bold the dish name (after the number + optional emoji), leaving the emoji
# outside the bold wrapper so it stays visible. Emoji/symbols are skipped via a
# permissive "leading decoration" group (digits/spaces/emoji/symbols).
_ITEM_RE = re.compile(
    r"^(\s*\d+\.\s*[^A-Za-zÀ-ỹ0-9]*)(\S.*?)(\s*[-\u2013\u2014]\s*)",
    re.MULTILINE | re.UNICODE,
)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _esc_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_michelin_bold(text: str, mode: str = "html") -> str:
    """Bold each numbered item's dish name.

    mode="html" -> Telegram HTML (<b>name</b>), rendered via ParseMode.HTML.
    mode="mdv2" -> Telegram MarkdownV2 (*name*), rendered via ParseMode.MARKDOWN_V2.
    Returns the original text unchanged if it has no numbered items.
    """
    text = _nfc(text)
    opener, closer = ("<b>", "</b>") if mode == "html" else ("*", "*")
    out_lines = []
    for line in text.splitlines():
        m = _ITEM_RE.match(line)
        if m:
            prefix, name, sep = m.group(1), m.group(2).strip(), m.group(3)
            name_esc = _esc_html(name) if mode == "html" else name
            out_lines.append(f"{prefix}{opener}{name_esc}{closer}{sep}{line[m.end():]}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def bold(text: str, mode: str = "html") -> str:
    """Bold an arbitrary string. NFC-normalized; default HTML mode for safety."""
    text = _nfc(text)
    if mode == "html":
        return f"<b>{_esc_html(text)}</b>"
    return f"*{text}*"
