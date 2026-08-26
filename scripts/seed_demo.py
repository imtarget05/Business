#!/usr/bin/env python
"""Seed demo data for local development and Docker environments.

Creates (idempotent):
- Pilot organization ("Pilot Org", slug: pilot-org)
- Demo customer ("Acme Corp", email: contact@acme.example)
- Sample knowledge document ("Refund Policy", source_type: manual)
- One demo conversation with 2 messages (user + assistant)

Usage:
    python scripts/seed_demo.py

Environment:
    Requires DATABASE_URL in .env (defaults to docker-compose postgres).
    Set PERSISTENCE_ENABLED=true for database writes.
"""

from __future__ import annotations

import asyncio
import sys
from uuid import UUID, uuid4

from packages.config.settings import get_settings
from packages.database.models import (
    Conversation,
    ConversationStatus,
    Customer,
    Document,
    DocumentStatus,
    Message,
    MessageRole,
    Organization,
    User,
)
from packages.database.repositories.conversations import ConversationRepository
from packages.database.repositories.documents import KnowledgeRepository, new_document
from packages.database.session import get_engine, get_session_factory, session_scope


PILOT_ORG_NAME = "Pilot Org"
PILOT_ORG_SLUG = "pilot-org"
PILOT_ORG_ID = UUID("11111111-1111-1111-1111-111111111111")

DEMO_CUSTOMER_NAME = "Acme Corp"
DEMO_CUSTOMER_EMAIL = "contact@acme.example"

DEMO_DOC_TITLE = "Refund Policy"
DEMO_DOC_CONTENT = """\
Business Ops Agent Swarm — Refund Policy (Demo)

1. Eligibility
   - Refunds are available within 30 days of purchase.
   - Digital products (licenses, subscriptions) are refundable within 14 days.
   - Custom development work is non-refundable once work has commenced.

2. Process
   - Submit a refund request via the support dashboard or email support@example.com.
   - Include your order number and reason for refund.
   - Refunds are processed within 5-10 business days to the original payment method.

3. Exceptions
   - Accounts with fraudulent activity are not eligible.
   - Refunds may be denied if the product has been extensively used (beyond evaluation).

4. Contact
   For questions, contact billing@example.com or open a support ticket.
"""

CONVERSATION_SUBJECT = "Demo: Refund inquiry"
CONVERSATION_CHANNEL = "web"
DEMO_MESSAGES = [
    ("user", "Hi, what is your refund policy for digital licenses?"),
    ("assistant", "Our refund policy for digital licenses allows refunds within 14 days of purchase. "
     "Please provide your order number and I can check eligibility. "
     "Refunds are processed to the original payment method within 5-10 business days."),
]


async def get_or_create_org() -> Organization:
    """Get or create the pilot organization."""
    async with session_scope() as session:
        # Check by slug first (unique constraint)
        from sqlalchemy import select
        stmt = select(Organization).where(Organization.slug == PILOT_ORG_SLUG)
        result = await session.execute(stmt)
        org = result.scalars().first()
        if org:
            print(f"✓ Organization exists: {org.name} ({org.id})")
            return org

        # Create with fixed UUID for reproducibility
        org = Organization(
            id=PILOT_ORG_ID,
            name=PILOT_ORG_NAME,
            slug=PILOT_ORG_SLUG,
        )
        session.add(org)
        await session.flush()
        print(f"✓ Created organization: {org.name} ({org.id})")
        return org


async def get_or_create_customer(org_id: UUID) -> Customer:
    """Get or create the demo customer."""
    async with session_scope() as session:
        from sqlalchemy import select
        stmt = select(Customer).where(
            Customer.organization_id == org_id,
            Customer.email == DEMO_CUSTOMER_EMAIL,
        )
        result = await session.execute(stmt)
        customer = result.scalars().first()
        if customer:
            print(f"✓ Customer exists: {customer.name} ({customer.id})")
            return customer

        customer = Customer(
            organization_id=org_id,
            name=DEMO_CUSTOMER_NAME,
            email=DEMO_CUSTOMER_EMAIL,
            notes="Demo customer for pilot organization",
        )
        session.add(customer)
        await session.flush()
        print(f"✓ Created customer: {customer.name} ({customer.id})")
        return customer


async def get_or_create_document(org_id: UUID) -> Document:
    """Get or create the sample knowledge document."""
    async with session_scope() as session:
        repo = KnowledgeRepository(session)
        doc = await repo.find_document_by_title(org_id, DEMO_DOC_TITLE)
        if doc:
            print(f"✓ Document exists: {doc.title} ({doc.id})")
            return doc

        doc = new_document(
            organization_id=org_id,
            title=DEMO_DOC_TITLE,
            source_type="manual",
            source_ref="seed_demo.py",
        )
        doc.status = DocumentStatus.embedded  # Mark as ready for search
        doc.chunk_count = 1
        await repo.add_document(doc)

        # Add a single chunk with mock embedding
        from packages.database.models import DocumentChunk
        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_index=0,
            content=DEMO_DOC_CONTENT,
            token_count=len(DEMO_DOC_CONTENT.split()),
            embedding=[0.1] * 768,  # Mock embedding
            chunk_metadata={"source": "seed_demo"},
        )
        await repo.add_chunks([chunk])
        await session.flush()
        print(f"✓ Created document: {doc.title} ({doc.id}) with 1 chunk")
        return doc


async def get_or_create_conversation(org_id: UUID) -> Conversation:
    """Get or create the demo conversation with messages."""
    async with session_scope() as session:
        repo = ConversationRepository(session)

        # Look for existing conversation with same subject
        from sqlalchemy import select
        stmt = select(Conversation).where(
            Conversation.organization_id == org_id,
            Conversation.subject == CONVERSATION_SUBJECT,
        )
        result = await session.execute(stmt)
        conv = result.scalars().first()
        if conv:
            # Check if messages exist
            messages = await repo.list_messages(org_id, conv.id)
            if len(messages) >= len(DEMO_MESSAGES):
                print(f"✓ Conversation exists with messages: {conv.subject} ({conv.id})")
                return conv
            # Fall through to add missing messages

        if not conv:
            conv = await repo.create_conversation(
                organization_id=org_id,
                channel=CONVERSATION_CHANNEL,
                subject=CONVERSATION_SUBJECT,
            )
            print(f"✓ Created conversation: {conv.subject} ({conv.id})")

        # Add messages (idempotent by sequence)
        for i, (role, content) in enumerate(DEMO_MESSAGES):
            existing = await repo.list_messages(org_id, conv.id)
            if len(existing) > i:
                continue  # Already have this message
            await repo.append_message(
                organization_id=org_id,
                conversation_id=conv.id,
                role=MessageRole(role),
                content=content,
            )
        print(f"✓ Added {len(DEMO_MESSAGES)} messages to conversation")
        return conv


async def main() -> int:
    print("=" * 60)
    print("Business Ops Agent Swarm — Demo Data Seeder")
    print("=" * 60)

    # Initialize DB connection
    settings = get_settings()
    get_session_factory(settings)

    # Verify database connectivity
    from packages.database.session import check_database
    if not await check_database():
        print("✗ Database connection failed. Check DATABASE_URL in .env")
        print("  For Docker: docker compose up -d db")
        return 1

    print(f"✓ Database connected: {settings.database_url.split('@')[-1]}")
    print()

    try:
        # Create entities in dependency order
        org = await get_or_create_org()
        await get_or_create_customer(org.id)
        await get_or_create_document(org.id)
        await get_or_create_conversation(org.id)

        print()
        print("=" * 60)
        print("✓ All demo data seeded successfully!")
        print("=" * 60)
        print()
        print("Summary:")
        print(f"  Organization: {PILOT_ORG_NAME} ({PILOT_ORG_ID})")
        print(f"  Customer:     {DEMO_CUSTOMER_NAME} <{DEMO_CUSTOMER_EMAIL}>")
        print(f"  Document:     {DEMO_DOC_TITLE}")
        print(f"  Conversation: {CONVERSATION_SUBJECT} ({CONVERSATION_CHANNEL})")
        print()
        print("Next steps:")
        print("  1. Start API:     uvicorn apps.api.main:app --reload")
        print("  2. Query KB:      curl -X POST http://localhost:8000/v1/tasks \\")
        print("                       -H 'Content-Type: application/json' \\")
        print("                       -d '{\"domain\":\"knowledge\",\"action\":\"query\",")
        print("                            \"payload\":{\"question\":\"refund policy\"}}'")
        print("  3. View dashboard: http://localhost:3000")
        return 0

    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        from packages.database.session import dispose_engine
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))