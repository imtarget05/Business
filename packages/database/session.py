"""Engine / session management.

Phase 0 keeps a module-level engine built from settings; Phase 1 may move to
connection pooling tuned per deployment target (local Docker vs Neon pooler).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from packages.config.settings import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is None:
        s = settings or get_settings()
        _engine = create_async_engine(s.database_url, echo=s.db_echo, future=True)
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(settings)
        # Windows CI/dev: psycopg async requires a selector event loop; sqlite
        # (aiosqlite) is unaffected. Harmless no-op elsewhere.
        if "postgresql" in str(engine.url):
            try:
                import asyncio
                import sys

                if sys.platform == "win32":
                    asyncio.set_event_loop_policy(
                        asyncio.WindowsSelectorEventLoopPolicy()
                    )
            except Exception:
                pass
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Cheap connectivity probe used by GET /ready."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
