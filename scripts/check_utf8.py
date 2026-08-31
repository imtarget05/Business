#!/usr/bin/env python
"""CI gate: fail the build if any .py source file is not valid UTF-8.

Why: emoji in menu strings get silently corrupted to mojibake (e.g.
'ð\x9f\x93\x9a') whenever a file is rewritten with the platform default
encoding on Windows (cp1252). That makes menu emoji vanish after updates.
This catches the corruption at CI time, before it ever ships.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Directories to skip (venv, caches, build artifacts).
SKIP = {".venv", ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", ".hermes"}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    bad = 0
    for p in root.rglob("*.py"):
        if any(part in SKIP for part in p.parts):
            continue
        try:
            p.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            bad += 1
            print(f"NON-UTF-8 SOURCE (emoji corruption risk): {p.relative_to(root)} -> {exc}")
    if bad:
        print(f"\n❌ {bad} file(s) not valid UTF-8. Re-save as UTF-8 (no BOM).")
        return 1
    print("✅ All .py sources are valid UTF-8 (emoji-safe).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
