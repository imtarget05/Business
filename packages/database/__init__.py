"""Database package: SQLAlchemy models + session management (STEP 0.6).

Neon PostgreSQL is the primary database; pgvector powers vector search.
No Supabase-specific APIs anywhere (ADR-002).
"""

from packages.database.base import Base
from packages.database.models import (
    Agent,
    AgentCapability,
    AgentRun,
    AuditLog,
    Document,
    DocumentChunk,
    Evaluation,
    Organization,
    Task,
    TaskStep,
    User,
)
from packages.database.session import dispose_engine, get_session, session_scope

__all__ = [
    "Agent",
    "AgentCapability",
    "AgentRun",
    "AuditLog",
    "Base",
    "Document",
    "DocumentChunk",
    "Evaluation",
    "Organization",
    "Task",
    "TaskStep",
    "User",
    "dispose_engine",
    "get_session",
    "session_scope",
]
