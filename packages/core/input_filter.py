"""Input Filter Layer (ADR-009).

Sanitizes raw user input BEFORE any LLM call or routing decision:
normalize -> strip control chars -> length cap -> spam/empty detection ->
prompt-injection detection -> PII masking -> language detection.

Pure Python, deterministic, zero LLM cost. Injected into the orchestrator;
blocked inputs short-circuit with REJECTED without consuming tokens.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, Field

from packages.config.settings import Settings, get_settings

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE = re.compile(r"[ \t]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_VN = re.compile(r"(?<!\d)(0|\+84)(3|5|7|8|9)\d{8}(?!\d)")
_ID_VN = re.compile(r"(?<!\d)\d{12}(?!\d)")
_CARD = re.compile(r"(?<!\d)\d{16}(?!\d)")

_URL = re.compile(r"https?://\S+")

_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions", "instruction_override"),
    (
        r"(?i)disregard\s+(the\s+)?(previous|prior|above)\s+(rules|instructions)",
        "instruction_override",
    ),
    (r"(?i)you\s+are\s+now\s+(a|an)\s+", "role_override"),
    (r"(?i)\b(system|developer)\s*(prompt|message)\s*:", "meta_prompt_probe"),
    (
        r"(?i)reveal|show|print\s+(me\s+)?(your\s+)?(system\s+prompt|instructions)",
        "prompt_leak",
    ),
    (r"<\|?(im_start|system|endoftext)\|?>", "special_token"),
)


class FilteredInput(BaseModel):
    """Sanitized result of the input filter pipeline."""

    clean_text: str
    original_text: str
    language: str = "unknown"  # vi | en | unknown
    is_spam: bool = False
    blocked: bool = False
    block_reason: str | None = None
    pii_masked: bool = False
    injection_detected: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


def detect_injection(text: str) -> tuple[bool, str | None]:
    """Detect common prompt-injection patterns. Returns (detected, pattern_id)."""
    for pattern, label in _INJECTION_PATTERNS:
        if re.search(pattern, text):
            return True, label
    return False, None


def mask_pii(text: str) -> tuple[str, bool]:
    """Mask emails, VN phone numbers, 12-digit IDs, card numbers.

    Keeps partial format, e.g. a***@gmail.com, 0912***678.
    """
    masked = False

    def _email_sub(m: re.Match) -> str:
        nonlocal masked
        masked = True
        local, domain = m.group(0).split("@", 1)
        return f"{local[0]}***@{domain}"

    def _tail4(m: re.Match) -> str:
        nonlocal masked
        masked = True
        s = m.group(0)
        return f"{s[:4]}***{s[-4:]}"

    text = _EMAIL.sub(_email_sub, text)
    text = _PHONE_VN.sub(_tail4, text)
    text = _ID_VN.sub(_tail4, text)
    text = _CARD.sub(_tail4, text)
    return text, masked


_VI_DETECT = re.compile(
    r"[ăâđêôơưáàảãạấầẩẫậéèẻẽẹếềểễệíìỉĩị"
    r"óòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]"
)


def _detect_language(text: str) -> str:
    diacritics = len(_VI_DETECT.findall(text.lower()))
    if diacritics >= 3:
        return "vi"
    ascii_letters = len(re.findall(r"[a-z]", text.lower()))
    if ascii_letters >= 3:
        return "en"
    return "unknown"


def _is_spam(text: str) -> bool:
    """Heuristic spam: repeated chars / excessive URLs / gibberish short tokens."""
    if not text.strip():
        return True
    if len(_URL.findall(text)) > 5:
        return True
    stripped = text.strip()
    if len(stripped) >= 10 and len(set(stripped.lower())) <= 3:
        return True  # e.g. "aaaaaaaaaa" / "1111!!!!!"
    return False


def filter_input(text: str, *, settings: Settings | None = None) -> FilteredInput:
    """Run the full sanitize pipeline. Synchronous by design (pure CPU)."""
    s = settings or get_settings()
    original = text or ""

    metadata: dict[str, Any] = {
        "original_length": len(original),
        "url_count": len(_URL.findall(original)),
        "email_count": len(_EMAIL.findall(original)),
    }

    if not s.input_filter_enabled:
        return FilteredInput(clean_text=original, original_text=original, metadata=metadata)

    # 1. Normalize
    clean = _CONTROL_CHARS.sub(" ", original)
    clean = _MULTI_SPACE.sub(" ", clean)
    clean = _MULTI_NEWLINE.sub("\n\n", clean).strip()

    # 2. Length cap
    truncated = len(clean) > s.input_max_chars
    clean = clean[: s.input_max_chars]
    metadata["truncated"] = truncated

    metadata["fingerprint"] = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]

    # 3. Spam / empty (length cap above already applied to `clean`)
    if _is_spam(clean):
        return FilteredInput(
            clean_text=clean,
            original_text=original,
            is_spam=True,
            blocked=True,
            block_reason="spam_or_empty",
            metadata=metadata,
        )

    # 4. Prompt-injection detection
    detected, label = detect_injection(clean)
    metadata["injection_pattern"] = label
    if detected:
        return FilteredInput(
            clean_text=clean,
            original_text=original,
            injection_detected=True,
            blocked=True,
            block_reason=f"prompt_injection:{label}",
            metadata=metadata,
        )

    # 5. PII masking
    pii_masked = False
    if s.pii_masking_enabled:
        clean, pii_masked = mask_pii(clean)

    return FilteredInput(
        clean_text=clean,
        original_text=original,
        language=_detect_language(clean),
        pii_masked=pii_masked,
        metadata=metadata,
    )


__all__ = ["FilteredInput", "filter_input", "mask_pii", "detect_injection"]
