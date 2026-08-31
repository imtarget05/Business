"""Conversation API routes (Phase 3, Task 3.4).

Endpoints:
- POST   /v1/conversations              — create a conversation (org_id from request)
- POST   /v1/conversations/{id}/messages — user msg → support agent → persist reply
- GET    /v1/conversations/{id}         — thread view (conversation + messages)
- GET    /v1/conversations/{id}/messages — messages only
"""

from __future__ import annotations

import uuid
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.support.agent import SupportAgent, create_support_agent
from agents.support.tools import create_support_tools
from apps.api.deps import current_org
from packages.config.settings import get_settings
from packages.contracts.enums import Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.errors import NotFoundError
from packages.core.tools import ToolRegistry, execute_tool_loop
from packages.database.repositories.conversations import ConversationRepository
from packages.database.session import get_session, get_session_factory
from packages.llm.factory import get_llm_provider
from packages.observability.logging import get_logger

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
logger = get_logger("conversations")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ConversationCreateRequest(BaseModel):
    """Request to create a new conversation."""

    channel: Literal["web", "email", "zalo", "facebook"] = Field(
        ..., description="Channel: web, email, zalo, or facebook"
    )
    subject: str | None = Field(None, max_length=512, description="Optional subject/title")


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
    messages: list[MessageResponse]


class MessageResponse(BaseModel):
    """Single message in a thread."""

    message_id: UUID
    sequence: int
    role: str
    content: str
    tool_metadata: dict | None = None


class ConversationListItem(BaseModel):
    """Conversation item for list view."""

    conversation_id: UUID
    organization_id: UUID
    channel: str
    status: str
    subject: str | None = None
    updated_at: str | None = None


class ConversationListResponse(BaseModel):
    """Response for listing conversations."""

    conversations: list[ConversationListItem]


# Forward reference resolution
ConversationThreadResponse.model_rebuild()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_support_agent_with_actions(
    agent: SupportAgent,
    conversation_id: UUID,
    org_id: UUID,
    user_message: str,
    channel: str,
    max_rounds: int,
    task_id: UUID,
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
        task_id=task_id,
    )

    # Build the prompt like the agent does
    prompt = agent._build_prompt(request)
    system = agent._system_prompt()

    actions: list[ActionMetadata] = []

    async def capture_action(name: str, arguments: dict, result: str, mode: str | None):
        action = ActionMetadata(
            tool=name,
            arguments=arguments,
            result=result,
            mode=mode,
        )
        actions.append(action)

    # Run the tool loop using the canonical execute_tool_loop with callback
    assistant_reply = await execute_tool_loop(
        provider=agent.llm,
        prompt=prompt,
        registry=agent.registry,
        system=system,
        max_rounds=max_rounds,
        temperature=0.2,
        max_tokens=1024,
        on_tool_call=capture_action,
        organization_id=org_id,
    )

    return assistant_reply, actions


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=ConversationCreateResponse, status_code=201)
async def create_conversation(
    body: ConversationCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> ConversationCreateResponse:
    """Create a new conversation thread."""
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
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> MessageCreateResponse:
    """Append a user message, run the support agent, persist assistant reply + actions."""
    repo = ConversationRepository(db)

    # Org is bound server-side from the caller's API key.
    effective_org = org_id

    # Verify conversation exists and belongs to org
    conv = await repo.get_conversation(effective_org, conversation_id)
    if conv is None:
        raise NotFoundError("conversation not found", task_id=uuid.uuid4())

    # Append user message
    user_msg = await repo.append_message(
        organization_id=effective_org,
        conversation_id=conversation_id,
        role="user",
        content=body.content,
    )
    if user_msg is None:
        raise NotFoundError("conversation not found", task_id=uuid.uuid4())

    # Create support agent with session factory for this request
    settings = get_settings()
    session_factory = get_session_factory(settings)
    agent = create_support_agent(llm=get_llm_provider(settings))
    # Replace tools with session-factory-aware versions
    agent._tools = create_support_tools(session_factory)
    agent._registry = ToolRegistry(*agent._tools)

    # Generate a task_id for this agent run to propagate into errors
    task_id = uuid.uuid4()

    # Run support agent on the user message
    assistant_reply, actions = await _run_support_agent_with_actions(
        agent=agent,
        conversation_id=conversation_id,
        org_id=effective_org,
        user_message=body.content,
        channel=conv.channel,
        max_rounds=settings.agent_max_tool_rounds,
        task_id=task_id,
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
        organization_id=effective_org,
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_reply,
        tool_metadata=tool_metadata,
    )
    if assistant_msg is None:
        raise NotFoundError("conversation not found", task_id=task_id)

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
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> ConversationThreadResponse:
    """Get full conversation thread with all messages."""
    repo = ConversationRepository(db)
    conv = await repo.get_conversation(org_id, conversation_id)
    if conv is None:
        raise NotFoundError("conversation not found")

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
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
) -> list[MessageResponse]:
    """Get messages for a conversation (org-scoped)."""
    repo = ConversationRepository(db)
    conv = await repo.get_conversation(org_id, conversation_id)
    if conv is None:
        raise NotFoundError("conversation not found")

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


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    request: Request,
    db: AsyncSession = Depends(get_session),
    org_id: UUID = Depends(current_org),
    limit: int = 50,
    offset: int = 0,
) -> ConversationListResponse:
    """List conversations for the organization, ordered by updated_at desc."""
    repo = ConversationRepository(db)
    conversations = await repo.list_conversations(org_id, limit=limit, offset=offset)

    return ConversationListResponse(
        conversations=[
            ConversationListItem(
                conversation_id=c.id,
                organization_id=c.organization_id,
                channel=c.channel,
                status=c.status.value,
                subject=c.subject,
                updated_at=c.updated_at.isoformat() if c.updated_at else None,
            )
            for c in conversations
        ]
    )
