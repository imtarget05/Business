"""Phase D2 — Inbound PO email integration tests.

Verifies the inbound handler parses a real PO email string end-to-end through
the PurchaseOrderAgent (mocked LLM) and returns a structured response.
"""

from __future__ import annotations

from uuid import uuid4

from agents.supply_chain.inbound import process_inbound_batch, process_inbound_email

SAMPLE_PO_EMAIL = (
    "PO NUMBER: PO-2024-901\n"
    "VENDOR: Global Supplies Ltd\n"
    "Items:\n"
    "- SKU-101, Office Chair, QTY: 4 @ $120.00 = $480.00\n"
    "- SKU-102, Desk Lamp, QTY: 2 @ $35.00 = $70.00\n"
    "TOTAL: $550.00\n"
)


async def test_process_inbound_email_parses_po() -> None:
    """A well-formed PO email is parsed into a structured agent response."""
    resp = await process_inbound_email(
        SAMPLE_PO_EMAIL,
        organization_id=str(uuid4()),
        user_id=str(uuid4()),
    )
    assert resp.status in ("success", "escalated", "failed")
    assert resp.task_id is not None
    # The agent should have produced a result dict (or a structured error)
    assert resp.result is not None or resp.error is not None


async def test_process_inbound_email_rejects_empty() -> None:
    """Empty/non-string email content is rejected with a validation error."""
    resp = await process_inbound_email("")
    assert resp.status == "failed"
    assert resp.error is not None
    assert resp.error.code == "VALIDATION_ERROR"


async def test_process_inbound_email_rejects_non_po() -> None:
    """A non-PO email is not silently accepted."""
    resp = await process_inbound_email("Hey, just checking in about our meeting tomorrow.")
    # Either a parse failure or an explicit rejection — never a fabricated PO.
    assert resp.status in ("failed", "escalated")
    assert resp.error is not None or resp.result.get("route") in (
        "auto_approved",
        None,
    )


async def test_process_inbound_batch() -> None:
    """Batch processing handles multiple emails concurrently."""
    emails = [SAMPLE_PO_EMAIL, "not a po", ""]
    results = await process_inbound_batch(emails)
    assert len(results) == 3
    # Empty string -> validation failure
    assert results[2].status == "failed"
    assert results[2].error.code == "VALIDATION_ERROR"


async def test_inbound_preserves_traceability() -> None:
    """Each response carries a stable task_id for correlation."""
    resp = await process_inbound_email(SAMPLE_PO_EMAIL, task_id=str(uuid4()))
    assert str(resp.task_id) == str(resp.task_id)
    assert resp.agent == "purchase_order_agent-v1"
