"""Integration tests for Supply Chain Agent — registry registration and inbound processing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.supply_chain.po_agent import PurchaseOrderAgent
from packages.config.settings import Settings
from packages.contracts.enums import Domain

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def supply_chain_agent():
    """Create a PO agent with mocked LLM and default settings."""
    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 100.0, "manager_b": 1000.0}
    return PurchaseOrderAgent(llm=AsyncMock(), settings=settings)


# ---------------------------------------------------------------------------
# Integration tests: agent registration patterns
# ---------------------------------------------------------------------------


def test_agent_descriptor_matches_domain(supply_chain_agent):
    """Agent descriptor must use Domain.SUPPLY_CHAIN and proper capabilities."""
    d = supply_chain_agent.descriptor
    assert d.domain == Domain.SUPPLY_CHAIN
    caps = d.capabilities
    assert "supply_chain.parse_po" in caps
    assert "supply_chain.classify_po" in caps
    assert "supply_chain.route_po" in caps


def test_agent_descriptor_capability_prefix(supply_chain_agent):
    """All capabilities must be prefixed with the agent's domain."""
    d = supply_chain_agent.descriptor
    for cap in d.capabilities:
        assert cap.startswith("supply_chain."), (
            f"Capability {cap!r} must start with 'supply_chain.' to match domain {d.domain.value!r}"
        )


def test_agent_descriptor_qualified_name(supply_chain_agent):
    """Qualified name should include version suffix per AgentDescriptor convention."""
    d = supply_chain_agent.descriptor
    # AgentDescriptor generates name like "purchase_order_agent-v1" from name + version
    assert "purchase_order_agent" in d.qualified_name
    assert d.qualified_name.endswith("-v1")


def test_agent_descriptor_timeout_and_retries(supply_chain_agent):
    """Agent must expose timeout_ms and max_retries for orchestrator use."""
    d = supply_chain_agent.descriptor
    assert d.timeout_ms == 30_000
    assert d.max_retries == 2


def test_settings_thresholds_flow_to_agent():
    """Agent must read po_approval_thresholds from settings."""
    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 50.0, "manager_b": 500.0}
    agent = PurchaseOrderAgent(llm=None, settings=settings)
    assert agent._settings is settings
    assert agent._settings.po_approval_thresholds == {"manager_a": 50.0, "manager_b": 500.0}


# ---------------------------------------------------------------------------
# Integration tests: inbound processing skeleton
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_single_email_processing(supply_chain_agent):
    """Inbound handler should process a single email through the agent pipeline."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    email_content = (
        "PO NUMBER: PO-2024-001\n"
        "VENDOR: Acme Corp\n"
        "Vendor Email: vendor@example.com\n"
        "Items:\n"
        "- SKU-001, Widget A - 10 units @ $5.00 each = $50.00 total\n"
        "TOTAL: $100.00"
    )

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": email_content},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "success"
    po = resp.result["po"]
    assert po["po_number"] == "PO-2024-001"
    assert po["vendor"] == "Acme Corp"
    assert len(po["items"]) >= 1
    assert po["total"] >= 50.0


@pytest.mark.asyncio
async def test_inbound_batch_processing_placeholder():
    """Batch inbound processing is a placeholder for future extension.

    Real batch implementation would:
    - Poll a message queue / Gmail API for pending emails
    - Process each email through the PO agent pipeline
    - Aggregate results for reporting
    - Requires Gmail API credential (manual setup by user)
    """
    # Placeholder: batch processing not yet implemented
    # Requires: Gmail API credential, message queue integration
    # Status: SKELETON - user must provide credential for real implementation
    pass


@pytest.mark.asyncio
async def test_inbound_invalid_email_content(supply_chain_agent):
    """Inbound handler should reject emails without PO data."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": "This is just a regular email, not a PO."},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "failed"


@pytest.mark.asyncio
async def test_inbound_missing_payload(supply_chain_agent):
    """Inbound handler should reject requests with missing email_content."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "rejected"


@pytest.mark.asyncio
async def test_inbound_empty_payload(supply_chain_agent):
    """Inbound handler should reject empty email content."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": ""},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "rejected"


@pytest.mark.asyncio
async def test_inbound_unsupported_action_rejected(supply_chain_agent):
    """Inbound handler should reject unsupported actions."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="unknown_action",
        payload={"email_content": "some content"},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "rejected"
    assert resp.error is not None
    assert "unsupported action" in resp.error.message


# ---------------------------------------------------------------------------
# Integration tests: routing and policy thresholds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_auto_approval_threshold(supply_chain_agent):
    """POs below manager_a threshold should be auto-approved."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    email_content = (
        "PO NUMBER: PO-2024-001\n"
        "VENDOR: Test Vendor\n"
        "Items:\n"
        "- SKU-001, Item A - 1 unit @ $25.00 each = $25.00 total\n"
        "TOTAL: $25.00"
    )

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="route_po",
        payload={"email_content": email_content},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["route"] == "auto_approved"
    assert resp.result["po"]["total"] == 25.0


@pytest.mark.asyncio
async def test_inbound_manager_a_approval_threshold(supply_chain_agent):
    """POs above manager_a threshold should require manager A approval."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    # Threshold manager_a = 100.0, PO total = 150.0 (above threshold)
    email_content = (
        "PO NUMBER: PO-2024-001\n"
        "VENDOR: Test Vendor\n"
        "Items:\n"
        "- SKU-001, Item A - 1 unit @ $150.00 each = $150.00 total\n"
        "TOTAL: $150.00"
    )

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="route_po",
        payload={"email_content": email_content},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["route"] == "approval_required_manager_a"


@pytest.mark.asyncio
async def test_inbound_manager_b_approval_threshold(supply_chain_agent):
    """POs above manager_b threshold should require manager B approval."""
    from uuid import uuid4

    from packages.contracts.models import TaskContext, TaskRequest

    email_content = (
        "PO NUMBER: PO-2024-001\n"
        "VENDOR: Test Vendor\n"
        "Items:\n"
        "- SKU-001, Item A - 1 unit @ $1500.00 each = $1500.00 total\n"
        "TOTAL: $1500.00"
    )

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="route_po",
        payload={"email_content": email_content},
        context=TaskContext(),
    )

    resp = await supply_chain_agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["route"] == "approval_required_manager_b"


# ---------------------------------------------------------------------------
# Integration tests: LLM fallback behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_llm_fallback_enabled():
    """When LLM is available but fails, agent must fall back to rule-based processing."""
    from uuid import uuid4

    from agents.supply_chain.po_agent import PurchaseOrderAgent
    from packages.config.settings import Settings
    from packages.contracts.models import TaskContext, TaskRequest

    settings = Settings()
    settings.po_approval_thresholds = {"manager_a": 100.0, "manager_b": 1000.0}

    # Use an async function that raises — simpler than AsyncMock side_effect
    async def failing_generate_structured(*args, **kwargs):
        raise Exception("LLM error")

    async def failing_generate(*args, **kwargs):
        raise Exception("LLM classification error")

    agent = PurchaseOrderAgent(llm=None, settings=settings)
    # Manually attach failing functions to simulate LLM failure
    agent._llm = type(
        "FakeLLM",
        (),
        {
            "generate_structured": failing_generate_structured,
            "generate": failing_generate,
            "provider_name": "fake",
        },
    )()

    email_content = "PO NUMBER: PO-2024-FALLBACK-TEST\nVENDOR: Fallback Test Vendor\nTotal: $150.00"

    req = TaskRequest(
        task_id=uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="parse_po",
        payload={"email_content": email_content},
        context=TaskContext(),
    )

    resp = await agent.handle(req)

    assert resp.status.value == "success"
    assert resp.result["po"]["po_number"] == "PO-2024-FALLBACK-TEST"
    assert resp.result["po"]["vendor"] == "Fallback Test Vendor"
    assert resp.result["po"]["total"] == 150.0
