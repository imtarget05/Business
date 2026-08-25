"""Conversation repository — multi-turn support threads.

Phase 3. All operations are organization-scoped: a foreign org id can never
read or mutate another org's conversations/messages (same contract as
KnowledgeRepository).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        organization_id: UUID,
        channel: str,
        subject: str | None = None,
    ) -> Conversation:
        conv = Conversation(
            organization_id=organization_id,
            channel=channel,
            status=ConversationStatus.open,
            subject=subject,
        )
        self._session.add(conv)
        await self._session.flush()
        return conv

    async def get_conversation(
        self, organization_id: UUID, conversation_id: UUID
    ) -> Conversation | None:
        conv = await self._session.get(Conversation, conversation_id)
        if conv is None or conv.organization_id != organization_id:
            return None
        return conv

    async def update_status(
        self,
        organization_id: UUID,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> Conversation | None:
        conv = await self.get_conversation(organization_id, conversation_id)
        if conv is None:
            return None
        conv.status = status
        await self._session.flush()
        return conv

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def append_message(
        self,
        organization_id: UUID,
        conversation_id: UUID,
        role: MessageRole,
        content: str,
        *,
        tool_metadata: dict | None = None,
    ) -> Message | None:
        """Append a message with the next sequence number for this thread."""
        conv = await self.get_conversation(organization_id, conversation_id)
        if conv is None:
            return None
        seq = await self._next_sequence(conversation_id)
        msg = Message(
            conversation_id=conversation_id,
            sequence=seq,
            role=role,
            content=content,
            tool_metadata=tool_metadata,
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def list_messages(
        self, organization_id: UUID, conversation_id: UUID
    ) -> list[Message]:
        """Messages in order (by sequence). Org-scoped via parent lookup."""
        conv = await self.get_conversation(organization_id, conversation_id)
        if conv is None:
            return []
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Conversation listing
    # ------------------------------------------------------------------

    async def list_conversations(
        self, organization_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        """List conversations for an organization, ordered by updated_at desc."""
        stmt = (
            select(Conversation)
            .where(Conversation.organization_id == organization_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------

    async def _next_sequence(self, conversation_id: UUID) -> int:
        # NOTE: This implementation has a potential race condition under high
        # concurrency. Multiple concurrent appends could read the same max
        # sequence and produce duplicate sequence numbers. A proper fix would
        # require a database-level sequence or SELECT FOR UPDATE, but is deferred
        # per YAGNI (schema change not needed for current load profile).
        stmt = select(func.max(Message.sequence)).where(
            Message.conversation_id == conversation_id
        )
        current = (await self._session.execute(stmt)).scalar_one()
        return (current or 0) + 1


__all__ = ["ConversationRepository", "ConversationStatus", "MessageRole"]
