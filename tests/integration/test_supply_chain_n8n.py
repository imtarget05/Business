"""Phase D tests: n8n client, circuit breaker, n8n node in supply chain graph."""

from __future__ import annotations

from uuid import uuid4

from agents.supply_chain.circuit_breaker import CircuitBreaker, CircuitState
from agents.supply_chain.n8n_client import N8nClient, N8nResult

# ---------------------------------------------------------------------------
# n8n client
# ---------------------------------------------------------------------------


async def test_n8n_disabled_without_url() -> None:
    client = N8nClient(enabled=False)
    assert client.enabled is False
    res = await client.export_po({"po": 1})
    assert res.exported is False
    assert res.error is None  # graceful no-op, not an error


async def test_n8n_export_posts_to_webhook() -> None:
    """With a fake httpx transport, verify POST is attempted and 200 -> exported."""
    import httpx

    def _handler(request):
        body = request.content
        assert b"po_data" in body or b"task_id" in body
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler=_handler)

    import agents.supply_chain.n8n_client as n8n_mod

    orig = n8n_mod.httpx.AsyncClient

    def _patched(**kwargs):
        kwargs.pop("timeout", None)
        return orig(transport=transport, timeout=5)

    n8n_mod.httpx.AsyncClient = _patched  # type: ignore[misc]
    client = N8nClient(
        webhook_url="http://n8n.local/webhook/po",
        enabled=True,
        timeout_seconds=5,
    )
    try:
        res = await client.export_po({"task_id": str(uuid4()), "po_data": {"x": 1}})
    finally:
        n8n_mod.httpx.AsyncClient = orig  # type: ignore[misc]

    assert isinstance(res, N8nResult)
    assert res.exported is True
    assert res.status_code == 200


async def test_n8n_export_handles_failure_gracefully() -> None:
    """A non-2xx or network error must NOT raise — returns exported=False."""
    import httpx

    def _handler(request):
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler=_handler)

    import agents.supply_chain.n8n_client as n8n_mod

    orig = n8n_mod.httpx.AsyncClient

    def _patched(**kwargs):
        return orig(transport=transport, timeout=5)

    n8n_mod.httpx.AsyncClient = _patched  # type: ignore[misc]
    client = N8nClient(webhook_url="http://n8n.local/x", enabled=True)
    try:
        res = await client.export_po({"po": 1})
    finally:
        n8n_mod.httpx.AsyncClient = orig  # type: ignore[misc]

    assert res.exported is False
    assert res.status_code == 500
    assert res.error is not None


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


async def test_circuit_breaker_opens_after_threshold() -> None:
    from agents.supply_chain.circuit_breaker import CircuitBreakerConfig

    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
    assert cb.state == CircuitState.CLOSED
    assert await cb.allow() is True
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # not yet at threshold
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    # While open, calls are not allowed
    assert await cb.allow() is False


async def test_circuit_breaker_recovers_after_timeout() -> None:
    import time

    from agents.supply_chain.circuit_breaker import CircuitBreakerConfig

    cb = CircuitBreaker(
        "test",
        CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=0.05),
    )
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert await cb.allow() is False
    time.sleep(0.1)
    # After timeout, half-open probe allowed
    assert await cb.allow() is True
    await cb.record_success()
    await cb.record_success()
    assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# n8n node in supply chain graph e2e
# ---------------------------------------------------------------------------


async def test_supply_chain_graph_includes_n8n_result() -> None:
    """Run the full supply chain graph; envelope must carry n8n_result key.

    An auto-approved PO below approval thresholds reaches the n8n export node.
    With no N8N_WEBHOOK_URL configured the client is disabled, so the result is
    a graceful no-op (not skipped, not an error).
    """
    from agents.supply_chain.graph import create_supply_chain_graph_orchestrator

    orch = create_supply_chain_graph_orchestrator()
    payload = {
        "email_content": (
            "PO NUMBER: PO-2024-001\n"
            "VENDOR: Test Vendor\n"
            "Items:\n"
            "- SKU-001, Widget, QTY: 10 @ $5.00 = $50.00\n"
            "TOTAL: $50.00\n"
        )
    }
    result = await orch.execute(uuid4(), payload, context={"organization_id": str(uuid4())})
    assert "n8n_result" in result
    assert result["n8n_result"] is not None
    # Auto-approved PO -> n8n export attempted; disabled client -> graceful no-op
    assert result["n8n_result"]["exported"] is False
    assert result["n8n_result"].get("error") is None
