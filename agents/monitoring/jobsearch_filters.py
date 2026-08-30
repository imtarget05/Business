# -*- coding: utf-8 -*-
"""Pure, testable filters for the JobSearch verification pipeline.

Extracted from ``telegram_bot._do_jobsearch_confirm`` so the spam-prone logic
(domain allowlist, detail-vs-listing detection, Apply-button verification) can be
unit-tested without Telegram / network.

The JobSearch pipeline must NEVER report a Google / Gmail / search-redirect URL
as a hiring result — those are not jobs. Only recruitment-domain detail pages
with a live Apply button may be marked VERIFIED.
"""

from __future__ import annotations

import re

# Recruitment domains only. NOTE: no free-text phrases here — every entry must be
# a real host suffix, otherwise `dom in _d` can never match and the filter is dead.
JOB_DOMAINS = (
    "topcv.vn",
    "itviec.com",
    "vietnamworks.com",
    "linkedin.com",
    "careerbuilder.vn",
    "careerviet.vn",
    "indeed.com",
    "careerjet.vn",
    "glassdoor.com",
    "vnw.vn",
    "timviecnhanh.com",
)

_APPLY_KW = ("apply", "ứng tuyển", "nộp đơn", "apply now")
_CLOSED_KW = ("đã đóng", "hết hạn", "expired", "closed", "not found", "404")


def _host(url: str) -> str:
    if not url:
        return ""
    if "//" in url:
        return url.split("//", 1)[1].split("/", 1)[0]
    return url.split("/", 1)[0]


def is_job_url(url: str) -> bool:
    """True only for links on a known recruitment domain (not google/mail/redirects)."""
    d = _host(url).lower().replace("www.", "")
    if not d:
        return False
    return any(d == dom or d.endswith("." + dom) or dom in d for dom in JOB_DOMAINS)


def is_job_detail(url: str) -> bool:
    """True for a job DETAIL page (not a listing/search/collection page)."""
    if not url:
        return False
    low = url.lower()
    # TopCV: detail pages are /viec-lam/<slug>.html
    if "topcv.vn/viec-lam/" in low and ".html" in low:
        return True
    # ITviec: detail pages are /it-jobs/<slug-with-hyphens>; bare /it-jobs is listing
    if "itviec.com/it-jobs/" in low:
        path = low.split("it-jobs/", 1)[1].split("?")[0].split("#")[0].strip("/")
        if path in ("machine-learning", "generative-ai", "python", "ai", "jobs"):
            return False
        return path.count("-") >= 2 and len(path) > 12
    if "linkedin.com/jobs/view/" in low:
        return True
    if "vietnamworks.com" in low and "/job" in low:
        return True
    # Explicitly reject listing / collection / search pages
    if "tim-viec-lam" in low:
        return False
    if "q=" in low and "indeed.com" in low:
        return False
    if low.endswith("/it-jobs") or low.endswith("/it-jobs/"):
        return False
    return False


def parse_title(html: str, fallback: str) -> tuple[str, str]:
    """Return (clean_title, raw_title) from page HTML, falling back to `fallback`."""
    raw = fallback
    if html:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            clean = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if clean and len(clean) > 10:
                return clean, clean
            raw = m.group(1).strip()
    return raw, raw


def verify_job_listing(url: str, html: str, now: str, fallback_title: str = "") -> dict:
    """Verify one listing page.

    Returns a dict with keys: title, status, confidence, evidence, is_detail.
    status is one of VERIFIED / UNCERTAIN / CLOSED.
    """
    is_detail = is_job_detail(url)
    title, _ = parse_title(html, fallback_title or _host(url))
    if not html:
        return {
            "title": title,
            "status": "UNCERTAIN",
            "confidence": 0.5,
            "evidence": "no html fetched",
            "is_detail": is_detail,
        }
    low_html = html.lower()
    has_apply = any(k in low_html for k in _APPLY_KW)
    is_closed = any(k in low_html for k in _CLOSED_KW)
    if not is_detail:
        return {
            "title": title,
            "status": "UNCERTAIN",
            "confidence": 0.55,
            "evidence": f"listing page, not detail — checked {now} — title: {title[:60]}",
            "is_detail": False,
        }
    if has_apply and not is_closed:
        return {
            "title": title,
            "status": "VERIFIED",
            "confidence": 0.92,
            "evidence": f"detail page 200 has Apply button, not closed — checked {now}",
            "is_detail": True,
        }
    if is_closed:
        return {
            "title": title,
            "status": "CLOSED",
            "confidence": 0.85,
            "evidence": "page indicates closed/expired",
            "is_detail": True,
        }
    return {
        "title": title,
        "status": "UNCERTAIN",
        "confidence": 0.65,
        "evidence": "detail page 200 but no clear Apply button",
        "is_detail": True,
    }
