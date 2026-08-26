"""Initial PostgreSQL schema (STEP 0.6).

- UUID primary keys, timestamps, FKs, constraints and sensible indexes.
- `document_chunks.embedding` uses pgvector (Vector column type) — plain
  PostgreSQL-standard, no Supabase-specific behaviour (ADR-002).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.database.base import Base, TimestampMixin


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    users: Mapped[list[User]] = relationship(back_populates="organization")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="users")


# ---------------------------------------------------------------------------
# Agent registry (persistent mirror of AgentDescriptor contracts)
# ---------------------------------------------------------------------------


class AgentStatusDB(StrEnum):
    active = "active"
    inactive = "inactive"
    degraded = "degraded"
    retired = "retired"


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"
    __table_args__ = (Index("uq_agents_name_version", "name", "version", unique=True),)

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[AgentStatusDB] = mapped_column(
        SAEnum(AgentStatusDB, name="agent_status_db", native_enum=False),
        default=AgentStatusDB.active,
        nullable=False,
    )
    timeout_ms: Mapped[int] = mapped_column(Integer, default=30_000, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    capabilities: Mapped[list[AgentCapability]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentCapability(Base, TimestampMixin):
    __tablename__ = "agent_capabilities"
    __table_args__ = (
        Index("uq_agent_capability", "agent_id", "capability", unique=True),
    )

    id: Mapped[UUID] = _uuid_pk()
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability: Mapped[str] = mapped_column(String(128), nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="capabilities")


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------


class TaskStatusDB(StrEnum):
    pending = "pending"
    classifying = "classifying"
    routing = "routing"
    running = "running"
    validating = "validating"
    completed = "completed"
    failed = "failed"
    escalated = "escalated"
    cancelled = "cancelled"
    dead_lettered = "dead_lettered"


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TaskStatusDB] = mapped_column(
        SAEnum(TaskStatusDB, name="task_status_db", native_enum=False),
        default=TaskStatusDB.pending,
        nullable=False,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskStepStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class TaskStep(Base, TimestampMixin):
    __tablename__ = "task_steps"

    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_task_step_sequence"),
    )

    id: Mapped[UUID] = _uuid_pk()
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[TaskStepStatus] = mapped_column(
        SAEnum(TaskStepStatus, name="task_step_status", native_enum=False),
        default=TaskStepStatus.pending,
        nullable=False,
    )
    input: Mapped[dict | None] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str | None] = mapped_column(
        String(64), index=True, default=None
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="steps")


# ---------------------------------------------------------------------------
# Execution / audit
# ---------------------------------------------------------------------------


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_task_attempt", "task_id", "attempt"),)

    id: Mapped[UUID] = _uuid_pk()
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_steps.id", ondelete="SET NULL"), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Append-only audit trail. No updates, no deletes."""

    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)  # user|system|n8n
    actor_id: Mapped[str | None] = mapped_column(String(255))
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


# ---------------------------------------------------------------------------
# Knowledge (Phase 2 RAG) — documents + document_chunks + pgvector
# ---------------------------------------------------------------------------


class DocumentStatus(StrEnum):
    pending = "pending"
    chunked = "chunked"
    embedded = "embedded"
    failed = "failed"


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, name="document_status", native_enum=False),
        default=DocumentStatus.pending,
        nullable=False,
        index=True,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


EMBEDDING_DIMENSIONS = 768  # Cloudflare @cf/baai/bge-base-en-v1.5 (Phase 2 default)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("uq_document_chunk_index", "document_id", "chunk_index", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    # pgvector column — standard PostgreSQL extension, no vendor lock-in.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    # DB column name is "metadata"; attribute renamed to avoid clashing with
    # SQLAlchemy's reserved Base.metadata attribute.
    chunk_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)

    document: Mapped[Document] = relationship(back_populates="chunks")


# ---------------------------------------------------------------------------
# Conversations (Phase 3 support agent) — multi-turn thread persistence
# ---------------------------------------------------------------------------


class ConversationStatus(StrEnum):
    open = "open"
    resolved = "resolved"
    escalated = "escalated"
    closed = "closed"


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)  # web|zalo|email|...
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, name="conversation_status", native_enum=False),
        default=ConversationStatus.open,
        nullable=False,
        index=True,
    )
    subject: Mapped[str | None] = mapped_column(String(512))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.sequence"
    )


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_message_sequence"),
    )

    id: Mapped[UUID] = _uuid_pk()
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, name="message_role", native_enum=False), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parent_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    # Optional tool call / action metadata (tool name, args, results).
    tool_metadata: Mapped[dict | None] = mapped_column(JSON)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Support Agent — customers + tickets (Phase 3, Task 3.3)
# ---------------------------------------------------------------------------


class Customer(Base, TimestampMixin):
    """Minimal customer record for support agent (YAGNI: no full CRM)."""

    __tablename__ = "customers"
    __table_args__ = (
        Index("uq_customer_email_org", "email", "organization_id", unique=True),
        Index("ix_customer_org_name", "organization_id", "name"),
    )

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class TicketStatus(StrEnum):
    open = "open"
    in_progress = "in_progress"
    waiting_customer = "waiting_customer"
    resolved = "resolved"
    closed = "closed"


class Ticket(Base, TimestampMixin):
    """Simple ticket record for support agent."""

    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_ticket_org_status", "organization_id", "status"),
        Index("ix_ticket_customer", "customer_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticket_status", native_enum=False),
        default=TicketStatus.open,
        nullable=False,
        index=True,
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    evaluator: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )