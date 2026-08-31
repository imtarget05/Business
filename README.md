# Business Ops Agent Swarm

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-900%2B-green.svg)](tests/)
[![License: Proprietary](https://img.shields.io/badge/license-proprietary-red.svg)](LICENSE)
[![RAG Recall@5](https://img.shields.io/badge/RAG%20Recall%20@5-100%25-brightgreen.svg)](docs/metrics.md)

> Multi-agent AI platform for business operations — orchestrating 15+ specialized agents
> with hybrid RAG, multi-provider LLM fallback, and production-grade observability.

## Overview

Business Ops Agent Swarm is a production-grade multi-agent system that automates complex
business workflows through domain-specific AI agents. Each agent handles a focused area —
from knowledge retrieval and customer support to sales automation, competitive intelligence,
and root-cause analysis — while a central orchestrator routes tasks using capability-based
routing and a learning loop that improves from user feedback.

The retrieval layer uses a **hybrid RAG** approach combining full-text search (FTS),
vector similarity, and Reciprocal Rank Fusion (RRF) to deliver citation-backed answers
with 100% recall@5. A **multi-provider LLM fallback chain** (Ollama → Cloudflare → Mock)
ensures zero-downtime operation even during provider outages.

Built for teams that need an extensible AI operations platform: add a new agent by
implementing a contract, register it, and the system routes to it automatically.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Next.js Dashboard (port 3000)                │
│   Control plane — task submission, agent status, metrics display    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST API
┌──────────────────────────────▼──────────────────────────────────────┐
│                      FastAPI Backend (port 8000)                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │ Input Filter │→│  Orchestrator │→│  Capability Router         │ │
│  │ (sanitize,   │  │ (LangGraph)  │  │  (keyword + learning loop) │ │
│  │  PII mask)   │  └──────┬───────┘  └────────────┬───────────────┘ │
│  └─────────────┘         │                       │                 │
│                          ▼                       ▼                 │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                    Agent Registry (15+ agents)                 │ │
│  │  Knowledge │ Support │ Sales │ Research │ Advisory │ RootCause │ │
│  │  Reporting │ OpsHub  │ Gmail │ Calendar │ YouTube │ Monitor  │ │
│  │  Competitor│ Context │ SupplyChain                          │ │
│  └───────────────────────────────────────────────────────────────┘ │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                     Data & Infrastructure Layer                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐ │
│  │ PostgreSQL+pgvector│ │  LLM Providers   │  │  Observability   │ │
│  │ (Neon / local)    │  │ Ollama→Cloudflare│  │ Prometheus       │ │
│  │ FTS + Vector + RRF│  │ →Mock fallback   │  │ Grafana          │ │
│  └──────────────────┘  └──────────────────┘  │ Structured logs  │ │
│                                              └──────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    External Integrations (n8n)                      │
│  Gmail inbound │ Slack webhooks │ Cron triggers │ Task relay       │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Features

- **15+ Specialized Agents** — Knowledge, Support, Sales, Research, Advisory, Root Cause, Reporting, Ops Hub, Gmail, Calendar, YouTube, Monitoring, Competitor, Context, Supply Chain
- **Hybrid RAG** — FTS + vector search with Reciprocal Rank Fusion, 100% recall@5
- **LLM Fallback Chain** — Ollama → Cloudflare → Mock, zero downtime on provider failure
- **Capability-Based Routing** — Registry-driven agent selection, no hardcoded conditionals
- **Learning Loop** — Feedback-driven routing improvements via `LearningEngine`
- **Input Filter Layer** — Prompt-injection detection, PII masking, spam rejection before LLM calls
- **Audit Trail** — Append-only event log with risk classification (READ/WRITE/DESTRUCTIVE)
- **Observability** — Prometheus metrics, Grafana dashboards, structured logging
- **n8n Integration** — Webhook triggers, AI-powered document routing, Gmail/Slack workflows
- **Telegram Bot** — NLP-powered support flow with session management
- **Multi-tenant** — Tenant isolation, API key auth, rate limiting
- **900+ Tests** — Unit, integration, E2E, and RAG evaluation coverage

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI, Python 3.11+ |
| Frontend | Next.js, React, Tailwind CSS |
| Database | PostgreSQL + pgvector (Neon) |
| LLM Framework | LangGraph, LangChain Core |
| Agents | 15+ domain-specific agents |
| LLM Providers | Ollama, Cloudflare AI, OpenAI-compatible, Mock |
| Observability | Prometheus, Grafana |
| Automation | n8n |
| Messaging | Telegram Bot API |
| Deployment | Docker, Docker Compose |
| Migrations | Alembic |
| Testing | pytest, pytest-asyncio |

## Quick Start

### Docker (recommended)

```bash
# 1. Copy env template
cp .env.example .env           # Linux/macOS
# copy .env.example .env       # Windows

# 2. Start all services (API, Web, Postgres+pgvector, n8n, Ollama)
docker compose up --build -d

# 3. Seed demo data
docker compose exec api python scripts/seed_demo.py

# 4. Open services
#    API docs:     http://localhost:8000/docs
#    Dashboard:    http://localhost:3000
#    n8n UI:       http://localhost:5678
```

### Local Development

```bash
# Backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -e ".[dev]"
cp .env.example .env
uvicorn apps.api.main:app --reload

# Frontend
cd apps/web
npm install
npm run dev                     # http://localhost:3000

# Database migrations
alembic upgrade head
```

With `LLM_PROVIDER=mock` the system runs with zero credentials and zero network calls.

### API Example

```bash
curl -X POST http://localhost:8000/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"domain":"knowledge","action":"query","payload":{"question":"What is our refund policy?"}}'
```

## RAG Evaluation

Hybrid retrieval evaluated against a golden dataset of 12 queries over 17 indexed documents
(Vietnamese geography, AI/ML, business operations, technology, agent architecture).

| Method | P@1 | P@3 | P@5 | Recall@5 | MRR |
|--------|-----|-----|-----|----------|-----|
| FTS      | 0.583 | 0.306 | 0.204 | **1.000** | 0.771 |
| VECTOR   | **0.667** | 0.250 | 0.167 | 0.833 | **0.757** |
| HYBRID   | 0.583 | **0.278** | **0.200** | **1.000** | 0.746 |

**Key findings:** The hybrid approach achieves perfect recall@5 while balancing precision
across both keyword and semantic signals. FTS alone matches hybrid recall; vector alone
leads on P@1. RRF fusion combines the best of both.

```bash
python scripts/run_rag_evaluation.py    # Re-run evaluation
```

## Testing

```bash
pytest                    # Run all 900+ tests
pytest tests/evaluation/  # RAG quality metrics only
pytest tests/unit/        # Unit tests only
pytest tests/integration/ # Integration tests only
pytest tests/e2e/         # End-to-end tests
ruff check .              # Lint
mypy packages agents apps # Type checking
```

Test categories: unit (agent logic, routing, retrieval), integration (API, auth, DB),
E2E (Telegram flow, supply chain approval, RAG cache warm), evaluation (RAG metrics).

## Roadmap

- Core architecture, contracts, orchestrator, registry
- Observability (Prometheus, Grafana), input filter, audit layer
- Learning loop, feedback-driven routing, reflection engine
- Root Cause Agent, evidence enrichment, Telegram NLP
- Multi-tenant SaaS, horizontal scaling, advanced analytics

## License

Proprietary — All rights reserved.
