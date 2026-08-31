"""Telegram chat persistence — wires bot conversations into PostgreSQL.

Uses the existing ConversationRepository (org-scoped, tested). Everything is
best-effort: persistence failures must NEVER break the bot's replies.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select

from packages.config.settings import get_settings
from packages.database.models import (
    Conversation,
    ConversationStatus,
    Customer,
    Message,
    Organization,
)
from packages.database.session import get_session_factory

logger = logging.getLogger(__name__)

_DEFAULT_ORG_SLUG = "telegram"
_DEFAULT_ORG_NAME = "Telegram Workspace"


class ChatMemory:
    """Persist Telegram chats and recall recent history for context."""

    def __init__(self) -> None:
        self._factory = get_session_factory(get_settings())

    async def log_user(self, chat_id: int, tg_user, text: str) -> None:
        await self._log(chat_id, tg_user, "user", text)

    async def log_assistant(self, chat_id: int, tg_user, text: str) -> None:
        await self._log(chat_id, tg_user, "assistant", text[:6000])

    async def _log(self, chat_id: int, tg_user, role: str, text: str) -> None:
        try:
            async with self._factory() as session:
                org = await self._ensure_org(session)
                await self._ensure_customer(session, org.id, tg_user)
                conv = await self._get_or_create_conversation(session, org.id, chat_id)
                from packages.database.repositories.conversations import ConversationRepository

                await ConversationRepository(session).append_message(org.id, conv.id, role, text)
                await session.commit()
        except Exception:
            logger.exception("chat persistence failed (non-fatal)")

    async def _ensure_org(self, session) -> Organization:
        org = (
            await session.scalars(
                select(Organization).where(Organization.slug == _DEFAULT_ORG_SLUG)
            )
        ).first()
        if org is None:
            org = Organization(name=_DEFAULT_ORG_NAME, slug=_DEFAULT_ORG_SLUG)
            session.add(org)
            await session.flush()
        return org

    async def _ensure_customer(self, session, org_id: UUID, tg_user) -> Customer:
        """Track the Telegram user as a customer (habits/personalization source)."""
        email = f"telegram:{tg_user.id}@local"
        cust = (
            await session.scalars(
                select(Customer).where(Customer.organization_id == org_id, Customer.email == email)
            )
        ).first()
        name = (tg_user.full_name or f"TG-{tg_user.id}")[:255]
        if cust is None:
            cust = Customer(organization_id=org_id, name=name, email=email)
            session.add(cust)
            await session.flush()
        elif cust.name != name:
            cust.name = name
        return cust

    async def _get_or_create_conversation(
        self, session, org_id: UUID, chat_id: int
    ) -> Conversation:
        subject = f"telegram-chat-{chat_id}"
        conv = (
            await session.scalars(
                select(Conversation)
                .where(
                    Conversation.organization_id == org_id,
                    Conversation.subject == subject,
                    Conversation.status == ConversationStatus.open,
                )
                .order_by(Conversation.created_at.desc())
                .limit(1)
            )
        ).first()
        if conv is None:
            conv = Conversation(organization_id=org_id, channel="telegram", subject=subject)
            session.add(conv)
            await session.flush()
        return conv

    async def recent_history(self, chat_id: int, limit: int = 8) -> list[tuple[str, str]]:
        """Return the last `limit` (role, content) messages for this chat."""
        try:
            async with self._factory() as session:
                org = (
                    await session.scalars(
                        select(Organization).where(Organization.slug == _DEFAULT_ORG_SLUG)
                    )
                ).first()
                if org is None:
                    return []
                conv = (
                    await session.scalars(
                        select(Conversation)
                        .where(
                            Conversation.organization_id == org.id,
                            Conversation.subject == f"telegram-chat-{chat_id}",
                        )
                        .order_by(Conversation.created_at.desc())
                        .limit(1)
                    )
                ).first()
                if conv is None:
                    return []
                msgs = (
                    await session.scalars(
                        select(Message)
                        .where(Message.conversation_id == conv.id)
                        .order_by(Message.created_at.desc())
                        .limit(limit)
                    )
                ).all()
                return [
                    (m.role.value if hasattr(m.role, "value") else str(m.role), m.content)
                    for m in reversed(msgs)
                ]
        except Exception:
            logger.exception("history recall failed (non-fatal)")
            return ""

    async def customer_profile_blurb(self, tg_user) -> str:
        """Personalization hint from stored customer data (habits source)."""
        try:
            async with self._factory() as session:
                org = (
                    await session.scalars(
                        select(Organization).where(Organization.slug == _DEFAULT_ORG_SLUG)
                    )
                ).first()
                if org is None:
                    return ""
                cust = (
                    await session.scalars(
                        select(Customer).where(
                            Customer.organization_id == org.id,
                            Customer.email == f"telegram:{tg_user.id}@local",
                        )
                    )
                ).first()
                if cust is None:
                    return ""
                parts = [f"Tên: {cust.name}"]
                if cust.notes:
                    parts.append(f"Ghi chú: {cust.notes[:300]}")
                return "; ".join(parts)
        except Exception:
            return ""
