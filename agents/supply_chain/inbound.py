# -*- coding: utf-8 -*-
"""
Inbound Handler for Purchase Order emails (Phase SC).

Accepts raw email content (string) from various sources and routes it through
the PurchaseOrderAgent for parsing, classification, and routing.

SOURCES (in order of maturity):
  1. Manual/test input  — READY NOW.  Call `process_inbound_email()` directly
     with a string.  Used by tests and dev consoles.
  2. Queue / webhook    — SKELETON.  Stub functions exist below for integrating
     with an SQS-style queue or HTTP webhook; the orchestration is in place but
     the wire-up to a real message broker is TODO.
  3. Gmail API polling  — PLACEHOLDER.  Requires user-provided OAuth2
     credentials (refresh token, client ID, client secret).  See
     `settings.google_*` / `settings.gmail_*` fields.  Function
     `fetch_unread_gmail_messages()` is a stub — implement when credentials
     are available.

DO NOT attempt real Gmail OAuth setup or real email receive here — just
create the skeleton/placeholder for those parts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentResponse, ErrorDetail, TaskContext, TaskRequest
from packages.llm.mock import MockLLMProvider

from agents.supply_chain.po_agent import PurchaseOrderAgent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Single email processing
# ---------------------------------------------------------------------------

async def process_inbound_email(
    email_content: str,
    *,
    task_id: str | None = None,
    channel: str = "inbound_email",
    organization_id: str | None = None,
    user_id: str | None = None,
    po_agent: PurchaseOrderAgent | None = None,
    llm: Any = None,
    trace_id: str | None = None,
) -> AgentResponse:
    """
    Process a single inbound PO email.

    Args:
        email_content: Raw email body/text (string).  This is the minimal
            contract — the handler does not care whether it came from Gmail,
            a queue, or manual input.
        task_id: Optional explicit task ID.  Generated if omitted.
        channel: Source channel label (default ``"inbound_email"``).
        organization_id: Org scope for the task context.
        user_id: User scope for the task context.
        po_agent: Existing PurchaseOrderAgent instance to reuse.  If None,
            a new agent is created with a MockLLMProvider (safe for tests /
            dev; swap for a real LLM provider in production).
        llm: LLM provider to inject when creating a fresh po_agent.
        trace_id: Optional trace ID for observability.

    Returns:
        AgentResponse from the PurchaseOrderAgent.handle() call.
    """
    # Always generate a valid task_id for the response — even on validation
    # failure the caller needs a stable identifier to correlate errors.
    _task_id = task_id or uuid4()

    if not email_content or not isinstance(email_content, str):
        return AgentResponse(
            task_id=_task_id,
            agent="purchase_order_agent-v1",
            status=AgentResponseStatus.FAILED,
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message="email_content must be a non-empty string",
            ),
        )

    # Reuse or create the agent
    if po_agent is None:
        po_agent = PurchaseOrderAgent(
            llm=llm or MockLLMProvider(),
        )

    request = TaskRequest(
        task_id=task_id or uuid4(),
        domain=Domain.SUPPLY_CHAIN,
        action="process_po",
        payload={"email_content": email_content},
        context=TaskContext(
            user_id=UUID(user_id) if isinstance(user_id, str) else user_id,
            organization_id=UUID(organization_id) if isinstance(organization_id, str) else organization_id,
            channel=channel,
            trace_id=trace_id,
        ),
    )

    logger.info(
        "processing_inbound_po",
        extra={"task_id": str(request.task_id), "channel": channel},
    )
    return await po_agent.handle(request)


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

async def process_inbound_batch(
    email_contents: list[str],
    *,
    concurrency: int = 5,
    **kwargs: Any,
) -> list[AgentResponse]:
    """
    Process multiple inbound PO emails concurrently.

    Args:
        email_contents: List of raw email body strings.
        concurrency: Max concurrent processing tasks (default 5).
        **kwargs: Forwarded to `process_inbound_email()` for each item.

    Returns:
        List of AgentResponse objects in the same order as input.
    """
    if not email_contents:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def _process_one(content: str, index: int) -> AgentResponse:
        async with semaphore:
            resp = await process_inbound_email(content, **kwargs)
            resp.metadata["batch_index"] = index
            return resp

    tasks = [_process_one(content, i) for i, content in enumerate(email_contents)]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Gmail API integration (PLACEHOLDER)
# ---------------------------------------------------------------------------
# ═══════════════════════════════════════════════════════════════════════════
# KNOWN GAPS — these require user-provided credentials; DO NOT implement yet:
#
# 1.  Google Cloud project with Gmail API enabled.
# 2.  OAuth2 credentials:
#       - google_oauth_client_id
#       - google_oauth_client_secret
#       - google_refresh_token  (obtained via one-time OAuth consent flow)
# 3.  Settings populated in .env:
#       - gmail_send_enabled = true   (if you also want to send replies)
#       - The above OAuth fields.
#
# The functions below are STUB — they will raise NotImplementedError until
# the user supplies credentials and elects to implement the real integration.
# ═══════════════════════════════════════════════════════════════════════════

async def fetch_unread_gmail_messages(
    query: str = "is:unread category:primary",
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """
    Stub: fetch unread Gmail messages matching a query.

    REAL IMPLEMENTATION REQUIRES:
      - google_oauth_client_id
      - google_oauth_client_secret
      - google_refresh_token
      - Google Cloud project with Gmail API enabled

    Returns:
        List of message dicts (stub: NotImplementedError).
    """
    raise NotImplementedError(
        "fetch_unread_gmail_messages is a placeholder.  "
        "Gmail API credential setup is required before real email fetch works.  "
        "See module docstring for the credential checklist."
    )


async def fetch_gmail_message_body(message_id: str) -> str:
    """
    Stub: fetch the body text of a specific Gmail message.

    REAL IMPLEMENTATION REQUIRES:
      - Same OAuth2 credentials as fetch_unread_gmail_messages()
      - Gmail API messages.get endpoint

    Returns:
        Email body as string (stub: NotImplementedError).
    """
    raise NotImplementedError(
        "fetch_gmail_message_body is a placeholder.  "
        "Gmail API credential setup is required."
    )


# ---------------------------------------------------------------------------
# Queue / webhook stubs (SKELETON)
# ---------------------------------------------------------------------------

async def process_queue_messages(
    messages: list[dict[str, Any]],
    *,
    handler: Any = process_inbound_email,
) -> list[AgentResponse]:
    """
    Stub: process messages from an inbound queue (SQS-style).

    Expects each message dict to contain an ``"email_content"`` key (string).
    In a real deployment this would be wired to a message broker (SQS, Redis
    Streams, RabbitMQ, etc.).  The handler function is injectable for testing.

    Args:
        messages: List of message dicts with at least ``"email_content"``.
        handler: Callable that takes email_content and returns AgentResponse.

    Returns:
        List of AgentResponse objects.
    """
    results: list[AgentResponse] = []
    for msg in messages:
        content = msg.get("email_content")
        if content and isinstance(content, str):
            resp = await handler(content)
            resp.metadata["queue_message_id"] = msg.get("message_id")
            results.append(resp)
        else:
            results.append(
                AgentResponse(
                    task_id=msg.get("task_id", uuid4()),
                    agent="purchase_order_agent-v1",
                    status=AgentResponseStatus.REJECTED,
                    error=ErrorDetail(
                        code="VALIDATION_ERROR",
                        message="queue message missing email_content",
                    ),
                )
            )
    return results


__all__ = [
    "process_inbound_email",
    "process_inbound_batch",
    "fetch_unread_gmail_messages",
    "fetch_gmail_message_body",
    "process_queue_messages",
]
