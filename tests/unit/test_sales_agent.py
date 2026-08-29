# -*- coding: utf-8 -*-
"""Task 4 — Email-to-Proposal Automation unit tests.

Covers:
* ``classify_intent`` deterministic intent detection (quote / service / complaint).
* ``SalesAgent.process_email`` builds a proposal with correct structure:
  client, package, scope, timeline, price, currency, follow-up email.
* Price + currency are read from ``data/templates/pricing.json`` (deterministic).
* ``render_pdf`` produces **non-empty** valid PDF bytes (reportlab, offline).
* The PDF bytes start with the PDF magic header ``%PDF``.
* Capability ``sales.process_email`` registration (domain ``sales``).
* ``handle`` returns SUCCESS with ``pdf_bytes``; rejects missing email / bad action.
* Registry resolves ``sales.process_email`` to the sales agent.

All tests are fast and offline (no network/model). The PDF is generated with
reportlab, which is a hard dependency of Task 4.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from agents.sales.agent import (
    SalesAgent,
    classify_intent,
    create_sales_agent,
    render_pdf,
)
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentResponse, TaskRequest
from packages.llm.mock import MockLLMProvider

# A realistic customer email (VN) requesting a launch proposal.
SAMPLE_EMAIL = (
    "Chào bạn,\n\n"
    "Mình là Nguyễn Văn A, đại diện công ty TNHH Sao Mai.\n"
    "Chúng tôi đang chuẩn bị ra mắt thương hiệu và cần báo giá gói "
    "Launch Impact vào tháng 8 này.\n\n"
    "Trân trọng,\nNguyễn Văn A"
)


# --------------------------------------------------------------------------- #
# classify_intent
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Mình cần báo giá gói Launch Impact", "quote_request"),
        ("Cho mình xin quote dịch vụ tư vấn", "quote_request"),
        ("Khách hàng khiếu nại về chất lượng", "complaint"),
        ("Tôi muốn hoàn tiền gói vừa mua", "complaint"),
        ("Công ty cần dịch vụ ra mắt thương hiệu", "service_inquiry"),
        ("Bạn có proposal cho gói growth không?", "service_inquiry"),
        ("Xin chào, bạn khỏe không?", "other"),
    ],
)
def test_classify_intent(text: str, expected: str) -> None:
    assert classify_intent(text) == expected


# --------------------------------------------------------------------------- #
# Agent construction / capability
# --------------------------------------------------------------------------- #
def test_agent_registers_sales_process_email_capability() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    assert agent.descriptor.domain is Domain.SALES
    assert "sales.process_email" in agent.descriptor.capabilities
    assert agent.descriptor.qualified_name == "sales-v1"


def test_factory_builds_agent() -> None:
    agent = create_sales_agent(llm=MockLLMProvider())
    assert isinstance(agent, SalesAgent)


# --------------------------------------------------------------------------- #
# process_email — structure, price, follow-up
# --------------------------------------------------------------------------- #
def test_process_email_structure_price_followup() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)

    # Intent classification picked up "báo giá".
    assert result.intent == "quote_request"
    # Client extracted from the signature line.
    assert "Nguyễn Văn A" in result.client
    # Package: email mentions "Launch Impact" -> launch_impact.
    assert result.package_key == "launch_impact"
    assert result.proposal_name == "Launch Impact"
    # Scope / timeline come from pricing.json (non-empty, deterministic).
    assert result.scope and "ra mắt" in result.scope.lower()
    assert result.timeline
    # Price read from pricing.json launch_impact = 180,000,000 VND.
    assert result.price == 180_000_000
    assert result.currency == "VND"
    # Proposal markdown rendered with placeholders substituted.
    assert "{{" not in result.proposal_markdown
    assert result.client in result.proposal_markdown
    # Follow-up email present with subject + body.
    assert result.follow_up.get("subject")
    assert result.follow_up.get("body")
    assert "Launch Impact" in result.follow_up["subject"]


def test_process_email_complaint_followup_differs() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    email = "Khách hàng khiếu nại dịch vụ rất tệ, tôi muốn hoàn tiền ngay."
    result = agent.process_email(email)
    assert result.intent == "complaint"
    # Complaint follow-up acknowledges, does not push a quote.
    assert "khiếu nại" in result.follow_up["subject"].lower()
    assert result.package_key == "launch_impact"  # default package


def test_process_email_unknown_package_defaults() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email("Chào bạn, cho mình xin báo giá nhé.")
    assert result.package_key == "launch_impact"  # default_package
    assert result.price == 180_000_000


def test_process_email_explicit_client_and_package() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(
        "Báo giá gói growth boost cho tôi",
        client="Công ty B",
        package_key="growth_boost",
    )
    assert result.client == "Công ty B"
    assert result.package_key == "growth_boost"
    assert result.price == 120_000_000


# --------------------------------------------------------------------------- #
# render_pdf — non-empty valid PDF bytes (offline, reportlab)
# --------------------------------------------------------------------------- #
def test_render_pdf_returns_nonempty_bytes() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)
    pdf = agent.render_pdf(result, result.brand)
    assert isinstance(pdf, bytes)
    assert len(pdf) > 0


def test_render_pdf_is_valid_pdf() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)
    pdf = agent.render_pdf(result, result.brand)
    # PDF files begin with the %PDF magic header.
    assert pdf[:5] == b"%PDF-"


def test_render_pdf_module_function_works() -> None:
    from agents.sales.agent import load_brand

    brand = load_brand()
    proposal = {
        "client": "Test Client",
        "proposal_name": "Starter",
        "scope": "Gói cơ bản: 10 bài content.",
        "timeline": "2 tuần",
        "price": 45_000_000,
        "currency": "VND",
    }
    pdf = render_pdf(proposal, brand)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 500


# --------------------------------------------------------------------------- #
# handle() — capability envelope
# --------------------------------------------------------------------------- #
async def test_handle_success_returns_pdf_bytes() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.SALES,
            action="process_email",
            payload={"email_text": SAMPLE_EMAIL},
        )
    )
    assert isinstance(resp, AgentResponse)
    assert resp.status is AgentResponseStatus.SUCCESS, resp.error
    assert "pdf_bytes" in resp.result
    assert isinstance(resp.result["pdf_bytes"], bytes)
    assert resp.result["pdf_bytes"][:5] == b"%PDF-"
    assert resp.result["price"] == 180_000_000
    assert resp.metadata["intent"] == "quote_request"


async def test_handle_missing_email_rejected() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.SALES,
            action="process_email",
            payload={},
        )
    )
    assert resp.status is AgentResponseStatus.REJECTED
    assert resp.error is not None


async def test_handle_unknown_action_rejected() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.SALES,
            action="send_quote",
            payload={"email_text": SAMPLE_EMAIL},
        )
    )
    assert resp.status is AgentResponseStatus.REJECTED


# --------------------------------------------------------------------------- #
# Registry wiring (capability resolves to the sales agent)
# --------------------------------------------------------------------------- #
async def test_registry_resolves_sales_process_email() -> None:
    from packages.core.registry import InMemoryAgentRegistry

    registry = InMemoryAgentRegistry()
    registry.register(
        SalesAgent(llm=MockLLMProvider()).descriptor,
        SalesAgent(llm=MockLLMProvider()),
    )
    desc, handler = registry.get_by_capability("sales.process_email")
    assert isinstance(handler, SalesAgent)
    assert desc.domain is Domain.SALES
    assert "sales.process_email" in desc.capabilities


# --------------------------------------------------------------------------- #
# Bootstrap wiring — sales agent registered in the real container
# --------------------------------------------------------------------------- #
async def test_bootstrap_registers_sales_agent() -> None:
    from packages.core.bootstrap import build_container

    ctn = build_container()
    desc, handler = ctn.registry.get_by_capability("sales.process_email")
    assert isinstance(handler, SalesAgent)
    assert desc.domain is Domain.SALES
