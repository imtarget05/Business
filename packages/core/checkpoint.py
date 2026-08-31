"""Persistent LangGraph checkpointing.

Single entry point: ``get_checkpointer(settings)``.

DSN resolution order (see :func:`resolve_checkpoint_dsn`):

1. ``settings.langgraph_checkpoint_url`` — explicit override, always wins.
2. ``settings.database_url`` — used when the application database is actually in
   use (``settings.persistence_enabled`` is true) and no explicit checkpoint URL
   is set. Previously a configured ``database_url`` was ignored here, so graph
   state silently stayed in memory even though a Postgres database was wired up
   (finding Du3: persistence lost, no error).
3. Nothing configured / non-Postgres DSN (e.g. ``sqlite+aiosqlite``) ->
   ``InMemorySaver``.

Why this module owns a lifecycle manager
----------------------------------------
``PostgresSaver.from_conn_string()`` is a ``@contextmanager`` classmethod: it
opens a psycopg connection, yields a ``PostgresSaver``, and closes the connection
on exit. Calling it *without* entering the context returns a
``contextlib._GeneratorContextManager``, not a saver — ``setup()`` then raises
``AttributeError`` and ``graph.compile(checkpointer=...)`` receives an object that
is not a checkpointer, so every graph run fails (finding F1). This module enters
the context manager and keeps it (and its connection) alive for the lifetime of
the process, closing it on interpreter exit via :func:`close_checkpointers`.

Failure policy
--------------
When a Postgres DSN *is* configured (explicitly or derived from
``database_url``) and the saver cannot be created or ``setup()`` fails, the error
is raised. Falling back to ``InMemorySaver`` there would hide state loss. The
in-memory saver is only returned when no Postgres DSN is configured at all.
"""

from __future__ import annotations

import atexit
import logging
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from packages.config.settings import Settings

logger = logging.getLogger(__name__)


# Guard the optional Postgres dependency so import never fails at startup.
try:
    from langgraph.checkpoint.postgres import PostgresSaver

    _POSTGRES_AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional dependency
    PostgresSaver = None  # type: ignore[assignment]
    _POSTGRES_AVAILABLE = False


# Schemes we accept as "Postgres" in a SQLAlchemy URL / libpq DSN.
_POSTGRES_SCHEMES = frozenset({"postgres", "postgresql"})

# Sources returned by resolve_checkpoint_dsn().
SOURCE_EXPLICIT = "langgraph_checkpoint_url"
SOURCE_DATABASE_URL = "database_url"
SOURCE_NONE = "none"


class PostgresCheckpointManager:
    """Owns the lifecycle of a ``PostgresSaver`` checkpointer.

    ``__init__`` *enters* the ``from_conn_string`` context manager and keeps both
    the manager and the entered saver alive; :meth:`checkpointer` returns the
    entered saver (never the context manager). :meth:`close` releases the
    underlying connection — it is called for every cached manager at process
    exit.

    :meth:`setup` deliberately does **not** swallow errors: the caller
    (:func:`get_checkpointer`) decides how to react, and a configured Postgres
    DSN must never degrade silently to in-memory state.
    """

    def __init__(self, conn_string: str) -> None:
        if not _POSTGRES_AVAILABLE:
            raise RuntimeError(
                "langgraph-checkpoint-postgres is not installed; "
                "cannot create a Postgres checkpointer."
            )
        self._conn_string = conn_string
        self._cm: Any | None = None
        self._saver: BaseCheckpointSaver | None = None
        self._enter()

    # -- lifecycle ----------------------------------------------------------

    def _enter(self) -> None:
        """Enter ``PostgresSaver.from_conn_string()`` and keep the saver."""
        cm: Any = PostgresSaver.from_conn_string(self._conn_string)

        if isinstance(cm, BaseCheckpointSaver):
            # Defensive: a future/patched API that returns a saver directly.
            self._cm = None
            saver: Any = cm
        else:
            enter = getattr(cm, "__enter__", None)
            if enter is None:
                raise TypeError(
                    "PostgresSaver.from_conn_string() returned "
                    f"{type(cm).__name__!r}, which is neither a checkpointer nor a "
                    "context manager; cannot build a Postgres checkpointer."
                )
            self._cm = cm
            saver = enter()

        # A non-saver here (e.g. the context manager itself) would be accepted by
        # graph.compile() and blow up at run time — fail loudly instead.
        if not isinstance(saver, BaseCheckpointSaver):
            self.close()
            raise TypeError(
                "PostgresSaver.from_conn_string() did not yield a "
                f"BaseCheckpointSaver (got {type(saver).__name__!r})."
            )
        self._saver = saver

    def close(self) -> None:
        """Exit the context manager / close the connection. Idempotent."""
        cm, self._cm = self._cm, None
        self._saver = None
        if cm is None:
            return
        try:
            cm.__exit__(None, None, None)
        except Exception as exc:  # pragma: no cover - depends on live DB
            logger.debug("Closing Postgres checkpointer failed: %s", exc)

    # -- accessors ----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._saver is not None

    @property
    def checkpointer(self) -> BaseCheckpointSaver:
        """The *entered* saver. Raises if it is not a real checkpointer."""
        saver = self._saver
        if not isinstance(saver, BaseCheckpointSaver):
            raise RuntimeError(
                "Postgres checkpointer is not open (close() was called or "
                "__init__ failed); refusing to hand out a non-saver."
            )
        return saver

    def setup(self) -> None:
        """Idempotently create the checkpoint tables. Raises on failure."""
        self.checkpointer.setup()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Process-wide manager cache (keeps the entered context managers alive)
# ---------------------------------------------------------------------------

_MANAGERS: dict[str, PostgresCheckpointManager] = {}
_MANAGERS_LOCK = threading.Lock()
_WARNED: set[str] = set()
_WARN_LOCK = threading.Lock()


def _get_or_create_manager(dsn: str) -> PostgresCheckpointManager:
    """Return the process-wide manager for ``dsn`` (created + set up once)."""
    with _MANAGERS_LOCK:
        existing = _MANAGERS.get(dsn)
        if existing is not None and existing.is_open:
            return existing

        manager = PostgresCheckpointManager(dsn)
        try:
            manager.setup()
        except Exception:
            # Never leak the psycopg connection when bootstrap fails.
            manager.close()
            raise
        _MANAGERS[dsn] = manager
        return manager


def close_checkpointers() -> None:
    """Close every open Postgres checkpointer. Idempotent; runs at exit."""
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
        _MANAGERS.clear()
    for manager in managers:
        manager.close()


atexit.register(close_checkpointers)


# ---------------------------------------------------------------------------
# DSN resolution
# ---------------------------------------------------------------------------


def _to_libpq_dsn(url: str | None) -> str | None:
    """Normalise a SQLAlchemy URL to a libpq DSN.

    ``postgresql+psycopg://u:p@h/db`` -> ``postgresql://u:p@h/db``.
    Returns ``None`` for empty values and non-Postgres URLs (e.g. sqlite), which
    psycopg cannot use.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme.split("+", 1)[0].lower() not in _POSTGRES_SCHEMES:
        return None
    return urlunsplit(("postgresql", parts.netloc, parts.path, parts.query, parts.fragment))


def resolve_checkpoint_dsn(settings: Settings) -> tuple[str | None, str]:
    """Return ``(dsn, source)`` for the LangGraph checkpointer.

    ``source`` is one of :data:`SOURCE_EXPLICIT`, :data:`SOURCE_DATABASE_URL` or
    :data:`SOURCE_NONE`. The explicit ``langgraph_checkpoint_url`` always wins;
    otherwise the application ``database_url`` is reused (Du3) whenever
    persistence is enabled, so a configured database also means durable graph
    checkpoints instead of silent in-memory state.
    """
    explicit = getattr(settings, "langgraph_checkpoint_url", None)
    if explicit:
        # Pass unknown schemes through untouched so the failure is explicit.
        return (_to_libpq_dsn(explicit) or explicit), SOURCE_EXPLICIT

    database_url = getattr(settings, "database_url", None)
    derived = _to_libpq_dsn(database_url)
    if derived is None:
        return None, SOURCE_NONE

    # ``database_url`` has a non-empty default, so "a Postgres DSN exists" is not
    # enough to conclude a database is actually in use; persistence_enabled is
    # this codebase's switch for "the application database is configured".
    if not getattr(settings, "persistence_enabled", False):
        _warn_once(
            "in-memory-checkpointer",
            "LangGraph checkpoints are kept IN MEMORY (lost on restart): "
            "persistence_enabled is false and langgraph_checkpoint_url is unset. "
            "Set PERSISTENCE_ENABLED=true (reuses database_url) or "
            "LANGGRAPH_CHECKPOINT_URL for durable checkpoints.",
        )
        return None, SOURCE_NONE

    return derived, SOURCE_DATABASE_URL


def _warn_once(key: str, message: str) -> None:
    with _WARN_LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    logger.warning(message)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def get_checkpointer(settings: Settings | None = None) -> BaseCheckpointSaver:
    """Return a LangGraph checkpointer for the given settings.

    - Postgres DSN configured (``langgraph_checkpoint_url``, or ``database_url``
      with ``persistence_enabled``) -> entered ``PostgresSaver``, tables set up.
    - nothing configured                                  -> ``InMemorySaver``.

    Raises ``RuntimeError`` when a Postgres DSN is configured but the saver
    cannot be opened or set up: silently returning ``InMemorySaver`` there would
    hide checkpoint/state loss.
    """
    if settings is None:
        from packages.config.settings import get_settings

        settings = get_settings()

    dsn, source = resolve_checkpoint_dsn(settings)
    if dsn is None:
        return InMemorySaver()

    if not _POSTGRES_AVAILABLE:
        message = (
            f"A Postgres checkpoint DSN is configured (source: {source}) but "
            "langgraph-checkpoint-postgres is not installed."
        )
        if source == SOURCE_EXPLICIT:
            raise RuntimeError(
                message + " Install it, or unset langgraph_checkpoint_url to "
                "accept in-memory (non-durable) checkpoints."
            )
        _warn_once(
            "postgres-package-missing",
            message + " Falling back to InMemorySaver: graph state is NOT durable.",
        )
        return InMemorySaver()

    try:
        manager = _get_or_create_manager(dsn)
    except Exception as exc:
        raise RuntimeError(
            "Failed to open the Postgres LangGraph checkpointer "
            f"(DSN source: {source}): {exc}. Refusing to fall back to an "
            "in-memory checkpointer because that silently loses graph state; "
            "fix the database/DSN, or clear langgraph_checkpoint_url and "
            "persistence_enabled to run intentionally without persistence."
        ) from exc

    return manager.checkpointer


__all__ = [
    "PostgresCheckpointManager",
    "get_checkpointer",
    "resolve_checkpoint_dsn",
    "close_checkpointers",
    "InMemorySaver",
    "SOURCE_DATABASE_URL",
    "SOURCE_EXPLICIT",
    "SOURCE_NONE",
    "_POSTGRES_AVAILABLE",
]
