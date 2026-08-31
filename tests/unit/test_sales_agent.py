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
        ("Tôi muốn đặt lịch tư vấn", "service_inquiry"),
        ("Tôi không hài lòng với sản phẩm", "complaint"),
        ("", "other"),
    ],
)
def test_classify_intent(text, expected) -> None:
    assert classify_intent(text) == expected


# --------------------------------------------------------------------------- #
# process_email — proposal structure
# --------------------------------------------------------------------------- #
def test_process_email_builds_proposal() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)
    assert result.client == "Nguyễn Văn A"
    assert result.package_key in {"starter", "growth", "launch_impact"}
    assert result.scope
    assert result.timeline
    assert result.price > 0
    assert result.currency == "VND"


def test_process_email_price_from_pricing_json() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)
    # Launch Impact package price from pricing.json
    assert result.price == 180_000_000


def test_process_email_follow_up_email() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)
    assert result.follow_up
    # follow_up is a dict with 'subject' and 'body' keys
    assert "subject" in result.follow_up
    assert "body" in result.follow_up
    assert "@" in result.follow_up["body"]


# --------------------------------------------------------------------------- #
# render_pdf — non-empty valid PDF bytes (offline, reportlab)
# --------------------------------------------------------------------------- #
def test_render_pdf_returns_nonempty_bytes() -> None:
    pytest.importorskip("reportlab")
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)
    pdf = agent.render_pdf(result, result.brand)
    assert isinstance(pdf, bytes)
    assert len(pdf) > 0


def test_render_pdf_is_valid_pdf() -> None:
    pytest.importorskip("reportlab")
    agent = SalesAgent(llm=MockLLMProvider())
    result = agent.process_email(SAMPLE_EMAIL)
    pdf = agent.render_pdf(result, result.brand)
    # PDF files begin with the %PDF magic header.
    assert pdf[:5] == b"%PDF-"


def test_render_pdf_module_function_works() -> None:
    pytest.importorskip("reportlab")
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
    pytest.importorskip("reportlab")
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


async def test_handle_bad_action_rejected() -> None:
    agent = SalesAgent(llm=MockLLMProvider())
    resp = await agent.handle(
        TaskRequest(
            task_id=_uuid.uuid4(),
            domain=Domain.SALES,
            action="spawn",
            payload={"email_text": SAMPLE_EMAIL},
        )
    )
    assert resp.status is AgentResponseStatus.REJECTED
    assert resp.error is not None


# --------------------------------------------------------------------------- #
# registry resolution
# --------------------------------------------------------------------------- #
def test_registry_resolves_sales_capability() -> None:
    from packages.core.registry import InMemoryAgentRegistry

    agent = create_sales_agent(llm=MockLLMProvider())
    registry = InMemoryAgentRegistry()
    registry.register(agent.descriptor, agent)
    resolved = registry.get_by_capability("sales.process_email")
    assert resolved is not None
    descriptor, agent = resolved
    assert descriptor.name == "sales"


def test_sales_agent_domain_is_sales() -> None:
    agent = create_sales_agent(llm=MockLLMProvider())
    assert agent.descriptor.domain is Domain.SALES
    assert "sales.process_email" in agent.descriptor.capabilities
