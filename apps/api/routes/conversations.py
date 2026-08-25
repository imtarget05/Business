"""Conversation API routes (Phase 3, Task 3.4).

Endpoints:
- POST   /v1/conversations              — create a conversation (org_id from request)
- POST   /v1/conversations/{id}/messages — append user message → run support agent → persist assistant reply + actions
- GET    /v1/conversations/{id}         — thread view (conversation + messages)
- GET    /v1/conversations/{id}/messages — messages only
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.support.agent import SupportAgent, create_support_agent
from agents.support.tools import create_support_tools
from packages.config.settings import get_settings
from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.tools import ToolRegistry, execute_tool_loop
from packages.database.repositories.conversations import ConversationRepository
from packages.database.session import get_session, get_session_factory
from packages.llm.factory import get_llm_provider
from packages.llm.mock import MockLLMProvider
from packages.observability.logging import get_logger

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
logger = get_logger("conversations")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ConversationCreateRequest(BaseModel):
    """Request to create a new conversation."""

    channel: str = Field(..., min_length=1, max_length=32, description="Channel: web, zalo, email, etc.")
    subject: str | None = Field(None, max_length=512, description="Optional subject/title")
    organization_id: UUID | None = Field(None, description="Organization ID (defaults to pilot org)")


class ConversationCreateResponse(BaseModel):
    """Response after creating a conversation."""

    conversation_id: UUID
    organization_id: UUID
    channel: str
    status: str
    subject: str | None = None


class MessageCreateRequest(BaseModel):
    """Request to append a user message and run the support agent."""

    content: str = Field(..., min_length=1, description="User message content")
    organization_id: UUID | None = Field(None, description="Organization ID (defaults to pilot org)")


class ActionMetadata(BaseModel):
    """Metadata for a tool action executed by the agent."""

    tool: str
    arguments: dict
    result: str
    mode: str | None = None  # e.g., "DRY_RUN" for send_email_reply


class MessageCreateResponse(BaseModel):
    """Response after appending a message and running the agent."""

    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    assistant_reply: str
    actions: list[ActionMetadata] = Field(default_factory=list)


class ConversationThreadResponse(BaseModel):
    """Full conversation thread with messages."""

    conversation_id: UUID
    organization_id: UUID
    channel: str
    status: str
    subject: str | None = None
    messages: list["MessageResponse"]


class MessageResponse(BaseModel):
    """Single message in a thread."""

    message_id: UUID
    sequence: int
    role: str
    content: str
    tool_metadata: dict | None = None


# Forward reference resolution
ConversationThreadResponse.model_rebuild()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_org(requested: UUID | None, db: AsyncSession) -> UUID:
    """Resolve organization_id from request or fall back to default org."""
    if requested is not None:
        return requested

    # Fall back to first organization in DB (dev/default behavior)
    from sqlalchemy import select

    from packages.database.models import Organization

    row = (
        await db.execute(select(Organization).order_by(Organization.created_at))
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail="organization_id is required (no default organization exists)",
        )
    return row.id


async def _run_support_agent_with_actions(
    agent: SupportAgent,
    conversation_id: UUID,
    org_id: UUID,
    user_message: str,
    channel: str,
) -> tuple[str, list[ActionMetadata]]:
    """Run the support agent on a user message and return (reply, actions_metadata).

    Captures tool calls executed during the tool loop.
    """
    # Build a TaskRequest for the support agent
    request = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",  # Default action for conversation messages
        payload={
            "subject": "Conversation message",
            "body": user_message,
            "conversation_id": str(conversation_id),
        },
        context=TaskContext(
            organization_id=org_id,
            channel=channel,
        ),
        task_id=uuid.uuid4(),
    )

    # Build the prompt like the agent does
    prompt = agent._build_prompt(request)
    system = agent._system_prompt()

    # Run the tool loop and capture the conversation history including tool calls
    conversation: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    actions: list[ActionMetadata] = []

    # Use the same loop logic as execute_tool_loop but capture tool calls
    max_rounds = 5
    for _ in range(max_rounds):
        response = await agent.llm.complete_with_tools(
            conversation,
            agent.registry.list_schemas(),
            system=system,
            temperature=0.2,
            max_tokens=1024,
        )
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            content = response.get("content")
            if not isinstance(content, str):
                raise RuntimeError("provider returned neither tool_calls nor text content")
            # Final answer
            return content, actions

        # Record assistant message with tool calls
        conversation.append(
            {
                "role": "assistant",
                "content": response.get("content"),
                "tool_calls": tool_calls,
            }
        )

        # Execute each tool call and capture metadata
        for call in tool_calls:
            name = call.get("name")
            if not isinstance(name, str):
                continue
            arguments = call.get("arguments") or {}
            tool = agent.registry.get(name)
            result = await tool.run(arguments)

            # Capture action metadata
            action = ActionMetadata(
                tool=name,
                arguments=arguments,
                result=result,
                mode=None,
            )
            # Extract DRY_RUN mode from send_email_reply results
            if name == "send_email_reply":
                try:
                    result_data = json.loads(result)
                    action.mode = result_data.get("mode")
                except Exception:
                    pass
            actions.append(action)

            # Add tool result to conversation
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": result,
                }
            )

    raise RuntimeError(f"tool-call loop did not converge after {max_rounds} rounds")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=ConversationCreateResponse, status_code=201)
async def create_conversation(
    body: ConversationCreateRequest,
    db: AsyncSession = Depends(get_session),
) -> ConversationCreateResponse:
    """Create a new conversation thread."""
    org_id = await _resolve_org(body.organization_id, db)

    repo = ConversationRepository(db)
    conv = await repo.create_conversation(
        organization_id=org_id,
        channel=body.channel,
        subject=body.subject,
    )
    await db.commit()

    logger.info(
        "conversation_created",
        extra={
            "conversation_id": str(conv.id),
            "organization_id": str(org_id),
            "channel": body.channel,
        },
    )

    return ConversationCreateResponse(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        channel=conv.channel,
        status=conv.status.value,
        subject=conv.subject,
    )


@router.post("/{conversation_id}/messages", response_model=MessageCreateResponse)
async def append_message(
    conversation_id: UUID,
    body: MessageCreateRequest,
    db: AsyncSession = Depends(get_session),
) -> MessageCreateResponse:
    """Append a user message, run the support agent, persist assistant reply + actions."""
    org_id = await _resolve_org(body.organization_id, db)

    repo = ConversationRepository(db)

    # Verify conversation exists and belongs to org
    conv = await repo.get_conversation(org_id, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    # Append user message
    user_msg = await repo.append_message(
        organization_id=org_id,
        conversation_id=conversation_id,
        role="user",
        content=body.content,
    )
    if user_msg is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    # Create support agent with session factory for this request
    settings = get_settings()
    session_factory = get_session_factory(settings)
    agent = create_support_agent(llm=get_llm_provider(settings))
    # Replace tools with session-factory-aware versions
    agent._tools = create_support_tools(session_factory)
    agent._registry = ToolRegistry(*agent._tools)

    # Run support agent on the user message
    assistant_reply, actions = await _run_support_agent_with_actions(
        agent=agent,
        conversation_id=conversation_id,
        org_id=org_id,
        user_message=body.content,
        channel=conv.channel,
    )

    # Append assistant message with tool metadata
    tool_metadata = {
        "actions": [
            {
                "tool": a.tool,
                "arguments": a.arguments,
                "result": a.result,
                "mode": a.mode,
            }
            for a in actions
        ]
    }
    assistant_msg = await repo.append_message(
        organization_id=org_id,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_reply,
        tool_metadata=tool_metadata,
    )
    if assistant_msg is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    await db.commit()

    logger.info(
        "message_appended",
        extra={
            "conversation_id": str(conversation_id),
            "organization_id": str(org_id),
            "user_message_id": str(user_msg.id),
            "assistant_message_id": str(assistant_msg.id),
            "actions_count": len(actions),
        },
    )

    return MessageCreateResponse(
        conversation_id=conversation_id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant_msg.id,
        assistant_reply=assistant_reply,
        actions=actions,
    )


@router.get("/{conversation_id}", response_model=ConversationThreadResponse)
async def get_conversation(
    conversation_id: UUID,
    organization_id: UUID | None = None,
    db: AsyncSession = Depends(get_session),
) -> ConversationThreadResponse:
    """Get full conversation thread with all messages."""
    org_id = await _resolve_org(organization_id, db)

    repo = ConversationRepository(db)
    conv = await repo.get_conversation(org_id, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    messages = await repo.list_messages(org_id, conversation_id)

    return ConversationThreadResponse(
        conversation_id=conv.id,
        organization_id=conv.organization_id,
        channel=conv.channel,
        status=conv.status.value,
        subject=conv.subject,
        messages=[
            MessageResponse(
                message_id=m.id,
                sequence=m.sequence,
                role=m.role.value,
                content=m.content,
                tool_metadata=m.tool_metadata,
            )
            for m in messages
        ],
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: UUID,
    organization_id: UUID | None = None,
    db: AsyncSession = Depends(get_session),
) -> list[MessageResponse]:
    """Get messages for a conversation (org-scoped)."""
    org_id = await _resolve_org(organization_id, db)

    repo = ConversationRepository(db)
    conv = await repo.get_conversation(org_id, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    messages = await repo.list_messages(org_id, conversation_id)

    return [
        MessageResponse(
            message_id=m.id,
            sequence=m.sequence,
            role=m.role.value,
            content=m.content,
            tool_metadata=m.tool_metadata,
        )
        for m in messages
    ]