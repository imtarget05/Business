"""Adversarial tests for the JobSearch verification filters.

These guard the pipeline against the historical failure mode: reporting Google /
Gmail / search-redirect URLs (or listing pages) as VERIFIED hiring results.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from agents.monitoring.jobsearch_filters import (
    is_job_detail,
    is_job_url,
    verify_job_listing,
)

# --- is_job_url: must reject EVERYTHING that is not a recruitment domain -------


def test_google_search_rejected():
    assert not is_job_url("https://www.google.com/search?q=ai+intern")


def test_google_mail_rejected():
    assert not is_job_url("https://mail.google.com/mail/u/0/#inbox")


def test_accounts_google_rejected():
    assert not is_job_url("https://accounts.google.com/o/oauth2/auth")



def test_vietnamworks_detail_accepted():
    assert is_job_url("https://www.vietnamworks.com/job/software-engineer-123")


def test_empty_url_rejected():
    assert not is_job_url("")


# --- is_job_detail: listing / collection pages must NOT be detail -------------


def test_topcv_listing_rejected():
    assert not is_job_detail("https://topcv.vn/viec-lam")


def test_topcv_detail_accepted():
    assert is_job_detail("https://topcv.vn/viec-lam/senior-ai-engineer-abc.html")


def test_itviec_bare_listing_rejected():
    assert not is_job_detail("https://itviec.com/it-jobs")


def test_itviec_collection_rejected():
    assert not is_job_detail("https://itviec.com/it-jobs/machine-learning")


def test_itviec_detail_accepted():
    assert is_job_detail("https://itviec.com/it-jobs/data-engineer-remote-99281")


def test_linkedin_detail_accepted():
    assert is_job_detail("https://www.linkedin.com/jobs/view/3838384848")


def test_linkedin_collection_rejected():
    assert not is_job_detail("https://www.linkedin.com/jobs/collections/recommended")


def test_indeed_search_rejected():
    assert not is_job_detail("https://www.indeed.com/jobs?q=ai+intern&l=HCMC")


def test_timvieclam_rejected():
    assert not is_job_detail("https://www.timviecnhanh.com/tim-viec-lam")


# --- verify_job_listing: only detail + Apply -> VERIFIED -----------------------


def test_detail_with_apply_is_verified():
    html = "<title>Senior AI Engineer - VinAI | TopCV</title><body><a href='/apply'>Apply now</a></body>"
    r = verify_job_listing(
        "https://topcv.vn/viec-lam/ai-eng-1.html", html, "2026-08-30T00:00:00+00:00"
    )
    assert r["status"] == "VERIFIED"
    assert r["confidence"] == 0.92
    assert "VinAI" in r["title"]


def test_listing_with_apply_stays_uncertain():
    # A listing page must NEVER be VERIFIED even if it shows an Apply button.
    html = "<title>Tuyển dụng AI - TopCV</title><body><a href='/apply'>Apply</a></body>"
    r = verify_job_listing("https://topcv.vn/viec-lam", html, "2026-08-30T00:00:00+00:00")
    assert r["status"] == "UNCERTAIN"
    assert r["is_detail"] is False


def test_detail_closed_is_closed():
    html = "<title>AI Intern - FPT | ITviec</title><body>vị trí đã đóng</body>"
    r = verify_job_listing("https://itviec.com/it-jobs/ai-intern-remote-99281", html, "now")
    assert r["status"] == "CLOSED"


def test_detail_no_apply_is_uncertain():
    html = "<title>AI Intern - FPT | ITviec</title><body>read more</body>"
    r = verify_job_listing("https://itviec.com/it-jobs/ai-intern-remote-99281", html, "now")
    assert r["status"] == "UNCERTAIN"
    assert r["confidence"] == 0.65


def test_no_html_is_uncertain():
    r = verify_job_listing("https://itviec.com/it-jobs/ai-intern-1", "", "now")
    assert r["status"] == "UNCERTAIN"


def test_google_url_never_verified_even_with_apply_html():
    # Hard guard: a non-job domain must not reach VERIFIED regardless of page content.
    html = "<title>Search</title><a href='/apply'>Apply</a>"
    r = verify_job_listing("https://www.google.com/search?q=x", html, "now")
    assert r["status"] != "VERIFIED"


def test_extract_keywords_keeps_core_intern():
    from agents.monitoring.jobsearch_filters import extract_job_keywords

    # 'ai intern' is the core keyword and must NOT be stripped.
    assert extract_job_keywords("tìm job AI intern gần đây") == "Ai Intern"
    assert "intern" in extract_job_keywords("tìm 5 job AI/ml intern tại Hà Nội").lower()


def test_extract_keywords_fallback():
    from agents.monitoring.jobsearch_filters import extract_job_keywords

    assert extract_job_keywords("") == "thực tập sinh AI"
    # only hiring verbs remain -> falls back to default (no empty keyword)
    assert extract_job_keywords("tìm job") == "thực tập sinh AI"


# --- parse_job_count: NO hardcoded '8' (Feature 1) ---------------------------


def test_parse_job_count_explicit():
    from agents.monitoring.jobsearch_filters import parse_job_count

    assert parse_job_count("tìm 5 job marketing hà nội") == 5
    assert parse_job_count("tìm 3 vị trí AI intern") == 3


def test_parse_job_count_none_when_not_stated():
    from agents.monitoring.jobsearch_filters import parse_job_count

    # no count stated -> must return None (never silently default to 8)
    assert parse_job_count("tìm việc marketing") is None
    assert parse_job_count("tìm marketing ở hà nội còn apply được") is None


def test_parse_job_count_ignores_phone_or_year():
    from agents.monitoring.jobsearch_filters import parse_job_count

    # a standalone number not tied to "tìm N job" must NOT be treated as count
    assert parse_job_count("liên hệ 0909123456") is None
    assert parse_job_count("tuyển từ 2024") is None


# --- extract_location: target the real location (Feature 2) -------------------


def test_extract_location_hanoi():
    from agents.monitoring.jobsearch_filters import extract_location

    assert extract_location("tìm marketing ở hà nội") == "Hà Nội"
    assert extract_location("tìm marketing tại hanoi") == "Hà Nội"
    assert extract_location("AI intern tại HCMC") == "Hồ Chí Minh"
    assert extract_location("remote backend developer") == "Remote"
    # no location stated -> None (caller must not silently default to a city)
    assert extract_location("tìm marketing còn apply được") is None


# --- select_candidates: return USEFUL ranked results, not empty (Feature 3) ---


def test_select_candidates_keeps_uncertain_when_no_verified():
    from agents.monitoring.jobsearch_filters import select_candidates

    verified = []
    uncertain = [
        {
            "job_title": "Marketing HN",
            "link": "https://topcv.vn/viec-lam/mkt-hn.html",
            "status": "UNCERTAIN",
            "match": 80,
            "company": "C1",
            "location": "Hà Nội",
        },
        {
            "job_title": "Marketing HCM",
            "link": "https://itviec.com/it-jobs/mkt-1",
            "status": "UNCERTAIN",
            "match": 70,
            "company": "C2",
            "location": "Hồ Chí Minh",
        },
    ]
    out = select_candidates(verified, uncertain, limit=5)
    # must NOT give up — returns ranked candidates even without VERIFIED
    assert len(out) == 2
    assert out[0]["job_title"] == "Marketing HN"
    assert all(j["status"] == "UNCERTAIN" for j in out)


def test_select_candidates_verified_ranked_first():
    from agents.monitoring.jobsearch_filters import select_candidates

    verified = [
        {
            "job_title": "V1",
            "link": "u1",
            "status": "VERIFIED",
            "match": 60,
            "company": "C",
            "location": "Hà Nội",
        },
    ]
    uncertain = [
        {
            "job_title": "U1",
            "link": "u2",
            "status": "UNCERTAIN",
            "match": 90,
            "company": "C",
            "location": "Hà Nội",
        },
    ]
    out = select_candidates(verified, uncertain, limit=5)
    # VERIFIED must come before higher-match UNCERTAIN
    assert out[0]["status"] == "VERIFIED"
    assert len(out) == 2


# --- context_job_keywords: use org memory to boost relevance (Feature 4) ------


def test_context_job_keywords_pulls_prior_intent():
    from agents.monitoring.jobsearch_filters import context_job_keywords

    items = [
        {"role": "user", "content": "tìm marketing hà nội"},
        {"role": "user", "content": "job ai intern"},
    ]
    kw = context_job_keywords(items)
    assert "marketing" in kw.lower()
    # empty memory -> empty string (no fake keywords)
    assert context_job_keywords([]) == ""


# --- extract_page_text: fetch via WebToolsProvider, fallback to httpx (3.2) ----


def test_extract_page_text_from_web_provider():
    from agents.monitoring.jobsearch_filters import extract_page_text

    class _FakeWeb:
        async def web_extract(self, urls, char_limit=5000):
            return {"results": [{"url": urls[0], "content": "<title>MKT Job</title> apply now"}]}

    import asyncio

    text = asyncio.run(extract_page_text("https://topcv.vn/viec-lam/mkt.html", _FakeWeb()))
    assert "apply" in text.lower()
    assert "MKT Job" in text


# --- confirm screen must NOT promise a hardcoded '8' (Feature 1.2) -----------


def test_confirm_screen_no_hardcoded_8():
    from agents.monitoring.jobsearch_filters import parse_job_count

    # brief says 5 -> screen should echo 5, never 8
    assert parse_job_count("tìm 5 job marketing hà nội") == 5
    # brief says nothing -> caller must show neutral text, not "8 vị trí"
    assert parse_job_count("tìm marketing ở hà nội") is None


def test_search_limit_driven_by_count():
    from agents.monitoring.jobsearch_filters import parse_job_count

    brief = "tìm 5 job marketing hà nội"
    limit = parse_job_count(brief) or 8  # default only at search-time, not promised to user
    assert limit == 5
    assert parse_job_count("tìm marketing hà nội") is None  # -> falls back to 8 at runtime
