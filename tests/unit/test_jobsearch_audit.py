# -*- coding: utf-8 -*-
"""Post-audit fixes for JobSearch STEP 2 (silent contract violations).

Covers:
- V1: searching label must not claim VERIFIED before verification
- V2: verify-gate — must NOT auto-send email when 0 verified / below promised count
- V3: each dropped item carries a reason code
- V4: dedupe by domain + URL path before display
- V5: honest header + note that core "still-open" question is unanswered
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from agents.monitoring.jobsearch_filters import (
    select_candidates,
    dedupe_candidates,
    decide_send_gate,
)


def _item(status, url, company="C", title="T", loc="Hà Nội", match=70):
    return {"status": status, "url": url, "company": company, "job_title": title,
            "location": loc, "match": match, "link": url, "required_skills": "",
            "evidence": "", "confidence": 0.6}


# --- V1: label is a STATE, not a claim of completion -------------------------

def test_searching_label_not_verified():
    # Contract: the searching message must not say VERIFIED before verifying.
    from agents.monitoring.jobsearch_filters import searching_label
    lbl = searching_label(8)
    assert "VERIFIED" not in lbl
    assert "đang tìm" in lbl.lower() or "tìm" in lbl.lower()


# --- V4: dedupe by domain + URL path -----------------------------------------

def test_dedupe_by_domain_path():
    items = [
        _item("UNCERTAIN", "https://topcv.vn/viec-lam"),
        _item("UNCERTAIN", "https://topcv.vn/viec-lam"),   # same domain+path -> dup
        _item("UNCERTAIN", "https://www.topcv.vn/viec-lam"),  # www equiv -> dup
        _item("UNCERTAIN", "https://itviec.com/it-jobs/x"),   # different -> keep
    ]
    out = dedupe_candidates(items)
    urls = {i["url"] for i in out}
    assert len(out) == 2, urls
    assert "https://itviec.com/it-jobs/x" in urls


# --- V3: drop reasons are explicit -------------------------------------------

def test_drop_reason_codes():
    from agents.monitoring.jobsearch_filters import classify_drop
    assert classify_drop("https://google.com/x") == "NON_JOB_DOMAIN"
    assert classify_drop("https://topcv.vn/viec-lam") == "NOT_DETAIL_PAGE"
    assert classify_drop("https://example.com") == "NON_JOB_DOMAIN"


# --- V2: verify-gate decides whether to ask before sending --------------------

def test_gate_zero_verified_blocks_send():
    # No VERIFIED items -> must ask user, do NOT auto-send.
    final = [_item("UNCERTAIN", "https://topcv.vn/viec-lam") for _ in range(4)]
    gate = decide_send_gate(promised=8, verified_count=0, final_list=final)
    assert gate["action"] == "ASK_USER"
    assert gate["reason"] == "NO_VERIFIED"


def test_gate_below_promised_blocks_send():
    # Promised 8, only 2 verified -> ask, do not silently send.
    final = [_item("VERIFIED", "https://topcv.vn/v1") for _ in range(2)]
    gate = decide_send_gate(promised=8, verified_count=2, final_list=final)
    assert gate["action"] == "ASK_USER"
    assert gate["reason"] == "BELOW_PROMISED"


def test_gate_ok_sends():
    final = [_item("VERIFIED", f"https://topcv.vn/v{i}") for i in range(8)]
    gate = decide_send_gate(promised=8, verified_count=8, final_list=final)
    assert gate["action"] == "SEND"


# --- V5: honest header reflects reality ---------------------------------------

def test_header_honest_when_unconfirmed():
    from agents.monitoring.jobsearch_filters import build_header
    h = build_header(verified_count=0, total=4)
    assert "CHƯA XÁC NHẬN" in h
    assert "chưa trả lời được" in h.lower() or "còn tuyển" not in h
