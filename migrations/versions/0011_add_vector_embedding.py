"""Add vector embedding columns (Feature 1: semantic / hybrid RAG).

Revision ID: 0011
Revises: 0010

What this migration does (PostgreSQL):

* ``kb_chunks``      -> ``ADD COLUMN embedding vector(768)`` + HNSW cosine index.
  Migration 0009 created the table WITHOUT that column, while
  ``packages.core.knowledge_base.KnowledgeBase`` reads and writes
  ``kb_chunks.embedding`` — so on Postgres every KB insert / vector query failed.
* ``michelin_facts`` -> ``ADD COLUMN embedding vector(768)`` + HNSW cosine index.

``document_chunks`` is deliberately NOT touched here: migration 0003 already
creates ``embedding Vector(768)`` on that table, so adding it again was redundant
(and the previous ``downgrade()`` dropped a column this revision never created,
silently breaking 0003's schema).

Implementation notes
--------------------
* ``vector(768)`` matches ``Settings.embedding_dimensions`` / ``embedding_dim``.
* HNSW indexes cannot be built inside a transaction block, and a plain
  ``CREATE INDEX`` takes an ACCESS EXCLUSIVE lock on the table. Both indexes are
  therefore created with ``CREATE INDEX CONCURRENTLY`` inside an autocommit block
  (``op.get_context().autocommit_block()``, which commits the migration
  transaction and switches the connection to ``isolation_level="AUTOCOMMIT"``).
* ``CREATE EXTENSION vector`` may need superuser rights on managed Postgres
  (Neon / RDS / Cloud SQL). It is attempted only when ``pg_extension`` does not
  already list it, and a failure aborts the migration with an explicit,
  actionable message instead of a silent half-migration.
* BACKFILL REQUIRED: rows written before this migration have
  ``embedding IS NULL`` and stay invisible to vector search until they are
  backfilled once an embedding provider is configured. ``upgrade()`` logs a
  warning with the exact batched UPDATE to run (no backfill happens here — it
  needs the embedding provider, which migrations do not have).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``CREATE INDEX ... IF NOT EXISTS``;
``downgrade()`` only uses ``DROP ... IF EXISTS``.

On non-PostgreSQL engines (SQLite dev/test) the column degrades to ``TEXT`` so
the Python-side cosine fallback keeps working.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.migration.0011_add_vector_embedding")

# Must match Settings.embedding_dimensions / Settings.embedding_dim.
DIM = 768

MICHELIN_TABLE = "michelin_facts"
KB_TABLE = "kb_chunks"

# (table, index name) — HNSW cosine indexes for the pgvector `<=>` operator.
EMBEDDING_TABLES: tuple[tuple[str, str], ...] = (
    (MICHELIN_TABLE, f"ix_{MICHELIN_TABLE}_embedding"),
    (KB_TABLE, f"ix_{KB_TABLE}_embedding"),
)

_EXTENSION_HELP = (
    "pgvector is required by migration 0011 but the `vector` extension is not "
    "installed and `CREATE EXTENSION vector` failed (managed Postgres usually "
    "requires a superuser / control-plane action). Ask a database admin to run "
    "`CREATE EXTENSION vector;` out-of-band in this database (Neon: enable the "
    "pgvector extension for the branch), then re-run `alembic upgrade head`."
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _table_exists(bind: sa.engine.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _column_exists(bind: sa.engine.Connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _vector_extension_installed(bind: sa.engine.Connection) -> bool:
    return bool(
        bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar()
    )


def _ensure_vector_extension(bind: sa.engine.Connection) -> None:
    """Make sure the pgvector extension exists, with a clear error if it cannot.

    Checked *before* attempting creation so a permission error does not poison
    the migration transaction when an admin already created it out-of-band.
    """
    if _vector_extension_installed(bind):
        return
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception as exc:  # pragma: no cover - needs a live Postgres
        log.error("%s Cause: %s", _EXTENSION_HELP, exc)
        raise RuntimeError(_EXTENSION_HELP) from exc


@contextmanager
def _autocommit() -> Iterator[None]:
    """Run statements outside the migration transaction (needed by CONCURRENTLY)."""
    ctx = op.get_context()
    block = getattr(ctx, "autocommit_block", None)
    if block is not None:  # Alembic >= 1.2 (pyproject pins >= 1.13)
        with block():
            yield
        return
    # Fallback: flip the raw connection to AUTOCOMMIT ourselves.
    connection = ctx.connection  # pragma: no cover - legacy Alembic only
    connection.execution_options(isolation_level="AUTOCOMMIT")
    yield


def _create_hnsw_indexes(tables: tuple[tuple[str, str], ...]) -> None:
    """Create HNSW cosine indexes concurrently (never inside a transaction)."""
    with _autocommit():
        for table, index_name in tables:
            statement = (
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
                f"ON {table} USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
            try:
                op.execute(statement)
            except Exception as exc:  # pragma: no cover - needs a live Postgres
                # CONCURRENTLY leaves an INVALID index behind on failure; the
                # column itself is what the application needs, so keep going and
                # tell the operator exactly how to repair the index.
                log.warning(
                    "Could not create index %s on %s: %s. The embedding column "
                    "works without it (sequential scan). To repair: "
                    "DROP INDEX IF EXISTS %s; then re-run: %s",
                    index_name,
                    table,
                    exc,
                    index_name,
                    statement,
                )


def _warn_backfill_needed(bind: sa.engine.Connection, tables: tuple[str, ...]) -> None:
    """Log the (manual) backfill required for pre-existing rows."""
    for table in tables:
        try:
            missing = bind.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE embedding IS NULL")
            ).scalar()
        except Exception as exc:  # pragma: no cover - needs a live Postgres
            log.warning("Could not count NULL embeddings in %s: %s", table, exc)
            continue
        if not missing:
            continue
        log.warning(
            "BACKFILL REQUIRED: %s row(s) in %s have embedding IS NULL and are "
            "invisible to vector search (the FTS path still finds them). "
            "Migrations have no embedding provider, so run a batched backfill "
            "once one is configured, e.g.: UPDATE %s SET embedding = :vec WHERE "
            "id IN (SELECT id FROM %s WHERE embedding IS NULL LIMIT 500); "
            "repeat until 0 rows remain.",
            missing,
            table,
            table,
            table,
        )


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name != "postgresql":
        # SQLite / other dev engines: TEXT column keeps the Python cosine path.
        for table, _index in EMBEDDING_TABLES:
            if _table_exists(bind, table) and not _column_exists(bind, table, "embedding"):
                op.add_column(table, sa.Column("embedding", sa.Text(), nullable=True))
        return

    _ensure_vector_extension(bind)

    indexable: list[tuple[str, str]] = []
    for table, index_name in EMBEDDING_TABLES:
        if not _table_exists(bind, table):
            log.warning(
                "Table %s does not exist; skipping its embedding column. "
                "Re-run this revision after the table is created.",
                table,
            )
            continue
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding vector({DIM})")
        indexable.append((table, index_name))

    _warn_backfill_needed(bind, tuple(table for table, _ in indexable))

    # Last: CONCURRENTLY commits the transaction above before running.
    _create_hnsw_indexes(tuple(indexable))


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name != "postgresql":
        for table, _index in EMBEDDING_TABLES:
            if _table_exists(bind, table) and _column_exists(bind, table, "embedding"):
                op.drop_column(table, "embedding")
        return

    with _autocommit():
        for _table, index_name in EMBEDDING_TABLES:
            try:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
            except Exception as exc:  # pragma: no cover - needs a live Postgres
                log.warning("Could not drop index %s: %s", index_name, exc)

    for table, _index in EMBEDDING_TABLES:
        if _table_exists(bind, table):
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS embedding")

    # document_chunks.embedding is owned by 0003 and intentionally left alone.
