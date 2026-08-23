# ADR-002: Neon PostgreSQL + pgvector

## Context

The platform needs a primary relational store plus vector search for the
knowledge domain. Supabase was considered but bundles Auth/Storage/Auto-APIs we
do not want to couple to, and its client libraries would leak vendor semantics
into the data layer.

## Decision

- Primary database: **Neon PostgreSQL** (serverless Postgres, free tier).
- Vector search: **pgvector** extension (natively supported by Neon).
- The database layer uses **PostgreSQL-standard interfaces only**: SQLAlchemy
  Core/ORM + psycopg driver + Alembic migrations. No Supabase Auth, Storage,
  or Supabase-specific APIs.
- Local development uses either the compose `pgvector/pgvector:pg16` container
  or a direct Neon connection string — identical SQL either way.

## Alternatives considered

1. **Supabase (Postgres + pgvector + auth)** — rejected: vendor-coupled auth &
   storage; harder to migrate; client SDK lock-in.
2. **Dedicated vector DB (Pinecone/Qdrant)** — rejected for now: extra service
   and cost; pgvector covers Phase 0–2 scale. Revisit at retrieval-quality
   stage.
3. **SQLite for dev** — rejected: divergent SQL and no pgvector, weakening
   migration fidelity.

## Consequences

- ✅ One engine for relational + vector; simple ops story on Neon free tier.
- ✅ Migrations are plain Alembic; portable to any managed Postgres.
- ⚠️ Vector index choice (IVFFlat vs HNSW) deferred until chunk volumes are
  real; column is created nullable so backfills are safe.
- ⚠️ Embedding dimension is fixed at 1536 in the model; changing providers may
  require a dimension migration.
