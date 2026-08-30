# -*- coding: utf-8 -*-
"""Adversarial tests for the JobSearch verification filters.

These guard the pipeline against the historical failure mode: reporting Google /
Gmail / search-redirect URLs (or listing pages) as VERIFIED hiring results.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from agents.monitoring.jobsearch_filters import (
    is_job_url,
    is_job_detail,
    verify_job_listing,
)


# --- is_job_url: must reject EVERYTHING that is not a recruitment domain -------

def test_google_search_rejected():
    assert not is_job_url("https://www.google.com/search?q=ai+intern")


def test_google_mail_rejected():
    assert not is_job_url("https://mail.google.com/mail/u/0/#inbox")


def test_accounts_google_rejected():
    assert not is_job_url("https://accounts.google.com/o/oauth2/auth")


def test_topcv_detail_accepted():
    assert is_job_url("https://topcv.vn/viec-lam/senior-ai-engineer-abc.html")


def test_itviec_detail_accepted():
    assert is_job_url("https://itviec.com/it-jobs/ai-intern-12345")


def test_linkedin_detail_accepted():
    assert is_job_url("https://www.linkedin.com/jobs/view/3838384848")


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
    r = verify_job_listing("https://topcv.vn/viec-lam/ai-eng-1.html", html, "2026-08-30T00:00:00+00:00")
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
