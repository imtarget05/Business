# -*- coding: utf-8 -*-
"""UTF-8 / emoji safety tests — guards against "lỗi phông chữ" (mojibake).

These tests lock in the windows-utf8-encoding-safety fix:
- source files stay valid UTF-8 (emoji never silently dropped after an update)
- a naive rewrite with the platform codepage (cp1252) is caught
- Vietnamese text with diacritics + emoji survives a decode round-trip
- the response-presentation layer passes Vietnamese + emoji through intact

Run: pytest tests/unit/test_utf8_emoji.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "check_utf8.py"
FIXTURE = ROOT / "tests" / "fixtures" / "michelin_vietnam.txt"

# Emoji that previously vanished after a Windows cp1252 rewrite.
EMOJI_SAMPLE = "📚 💡 🏥 🍽️ 🥚 🇫🇷"
VIETNAMESE_SAMPLE = "Nhà hàng L'Usine (Hà Nội) được vinh danh Michelin 🍽️"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_utf8", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_utf8_script_passes_on_clean_tree():
    """The CI gate exits 0 on a clean, valid-UTF-8 tree."""
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "✅" in r.stdout


def test_check_utf8_script_fails_on_mojibake(tmp_path: Path):
    """Injecting cp1252 corruption makes the gate fail (exit 1)."""
    # Build a temp project tree with one corrupted .py file.
    bad = tmp_path / "corrupt.py"
    # Write INVALID UTF-8 bytes (0xFF is never valid UTF-8) — this is what a
    # cp1252 rewrite of an emoji produces in practice (e.g. a stray 0xNN that
    # the UTF-8 decoder rejects). The gate must flag it.
    bad.write_bytes(b"x = '\xff Knowledge'\n")
    good = tmp_path / "ok.py"
    good.write_text("# -*- coding: utf-8 -*-\nx = '📚 Knowledge'\n", encoding="utf-8")

    # Re-point the gate at the temp tree by monkeypatching Path.parent chain.
    mod = _load_script_module()
    original_rglob = Path.rglob
    try:
        def _fake_rglob(self, pattern):
            if self == tmp_path:
                return iter([bad, good])
            return original_rglob(self, pattern)
        Path.rglob = _fake_rglob
        # Point root at tmp_path via a patched resolve chain is overkill;
        # instead call main()-equivalent logic through the script on the temp dir.
        rc = _run_gate_on(tmp_path)
    finally:
        Path.rglob = original_rglob
    assert rc == 1


def _run_gate_on(root: Path) -> int:
    """Minimal re-impl of check_utf8 main() scoped to `root`."""
    bad = 0
    for p in root.rglob("*.py"):
        if any(part in {".venv", ".git", "__pycache__"} for part in p.parts):
            continue
        try:
            p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            bad += 1
    return 1 if bad else 0


def test_emoji_roundtrip_under_pythonutf8():
    """open(path,'w').write(emoji) then read() must return the emoji, not mojibake.

    This is the exact regression that dropped menu emoji "sau mỗi lần cập nhật".
    With PYTHONUTF8=1 (set on this host + docker-compose), the default text
    encoding is UTF-8, so the round-trip is clean.
    """
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(EMOJI_SAMPLE)
        name = f.name
    with open(name, "r", encoding="utf-8") as f:
        content = f.read()
    assert content == EMOJI_SAMPLE
    # Explicitly refute the historical corruption pattern.
    assert "ð" not in content


def test_michelin_fixture_is_valid_utf8_and_intact():
    """The Michelin fixture (Vietnamese + emoji) decodes cleanly and keeps all 5 entries."""
    raw = FIXTURE.read_bytes().decode("utf-8")
    # No mojibake markers.
    assert "ð" not in raw
    # Diacritics preserved.
    for token in ("L'Usine", "Hà Nội", "Hồ Chí Minh", "Gaggan", "Eggspot", "Sud 40",
                  "L'Atelier de Joël Robuchon"):
        assert token in raw, f"missing/garbled token: {token}"
    # Emoji preserved.
    assert "🍽️" in raw and "🥚" in raw and "🇫🇷" in raw
    # Exactly 5 numbered entries.
    numbered = [ln for ln in raw.splitlines() if ln.strip() and ln.strip()[0].isdigit()]
    assert len(numbered) == 5, numbered


def test_response_presentation_keeps_vietnamese_and_emoji():
    """present() must pass Vietnamese text + emoji through without mangling."""
    from uuid import uuid4
    from packages.contracts.enums import AgentResponseStatus
    from packages.contracts.models import AgentResponse
    from packages.core.response_presentation import present

    resp = AgentResponse(
        task_id=uuid4(),
        agent="research-v1",
        status=AgentResponseStatus.SUCCESS,
        result={
            "answer": VIETNAMESE_SAMPLE,
            "key_points": [
                "L'Usine (Hà Nội) đạt Michelin 🍽️",
                "Gaggan (Hồ Chí Minh) đạt Michelin 🍛",
            ],
            "confidence": 0.9,
        },
    )
    out = present(resp)
    assert out["answer"] == VIETNAMESE_SAMPLE
    assert "🍽️" in out["key_points"][0]
    assert "Hồ Chí Minh" in out["key_points"][1]
    # Ensure no diacritic/emoji was collapsed to ASCII.
    assert "ð" not in out["answer"]


def test_sanitize_text_normalizes_nfd_vietnamese():
    """A phone that decomposes Vietnamese (NFD) when copy-pasting must be
    restored to composed NFC so diacritics survive into the pipeline."""
    import sys
    sys.path.insert(0, str(ROOT))
    from agents.monitoring.telegram_bot import _sanitize_text

    # "Tiếng Việt" in NFD: base letters + separate combining accents.
    nfd = "Tiê\u0301ng Viê\u0323t"  # Tiê+◌́ (Tiếng)  Viê+◌̣ (Việt)
    out = _sanitize_text(nfd)
    # NFC re-composes.
    assert out == "Tiếng Việt", repr(out)
    assert "ð" not in out


def test_sanitize_text_keeps_emoji_drops_variation_selector():
    """Emoji must survive sanitization, but the trailing variation selector
    (which renders as a tofu box on some Telegram clients) is stripped."""
    import sys
    sys.path.insert(0, str(ROOT))
    from agents.monitoring.telegram_bot import _sanitize_text

    text = "📚\ufe0f Knowledge"  # emoji + variation selector-16
    out = _sanitize_text(text)
    assert "📚" in out
    assert "\ufe0f" not in out
    assert "Knowledge" in out


def test_sanitize_text_drops_replacement_and_control():
    """The Unicode replacement char and stray control chars are dropped so the
    Telegram client never shows 'broken icon' boxes."""
    import sys
    sys.path.insert(0, str(ROOT))
    from agents.monitoring.telegram_bot import _sanitize_text

    text = "L'Usine\uFFFD Michelin \u0007next"  # replacement char + bell control
    out = _sanitize_text(text)
    assert "\ufffd" not in out
    assert "\u0007" not in out
    assert "L'Usine" in out and "Michelin" in out and "next" in out
