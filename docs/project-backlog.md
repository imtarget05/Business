# Project Backlog

> Structured action items for pre-interview sprint preparation.

---

## Backlog Table

### Deployment

| ID | Priority | Category | Task | Description | Dependencies | Status |
|---|---|---|---|---|---|---|
| DEP-001 | 🔴 High | Deployment | Deploy to Free Tier | Deploy `docker-compose.prod.yml` to Railway (free $5 credit) or Render (free tier). Steps: Create account → Connect GitHub repo → Set env vars → Deploy. | API-001 | ⬜ Not Started |

### API

| ID | Priority | Category | Task | Description | Dependencies | Status |
|---|---|---|---|---|---|---|
| API-001 | 🔴 High | API | Fix Knowledge Page API Mismatch | Frontend calls `/v1/knowledge/documents`, `/v1/knowledge/ingest`, `DELETE /v1/knowledge/documents/{id}` — backend only has `/v1/knowledge/index` and `/v1/knowledge/query`. Fix: Add missing routes to `apps/api/routes/knowledge.py` OR update frontend `apps/web/app/knowledge/page.tsx` to use existing routes. | None | ⬜ Not Started |

### n8n Workflows

| ID | Priority | Category | Task | Description | Dependencies | Status |
|---|---|---|---|---|---|---|
| N8N-001 | 🟡 Medium | n8n | Add Error Handling to Workflows | All 3 workflows (`inbound-task-relay.json`, `gmail-inbound-reply.json`, `ai-document-processor.json`) lack error handling. Add Error Trigger nodes → IF nodes → fallback actions (Slack alert, email, log to DB). `ai-document-processor.json` needs retry on Ollama failure + error branch on empty classification. | None | ⬜ Not Started |

### Testing

| ID | Priority | Category | Task | Description | Dependencies | Status |
|---|---|---|---|---|---|---|
| TEST-001 | 🟡 Medium | Testing | Add Unit Tests for OllamaProvider Chat | Create `tests/unit/test_ollama_provider.py` with: `generate()` with mocked httpx, `generate_structured()` with Pydantic schema, `complete_with_tools()` with tool call parsing, `_check_health()` success/failure, timeout + connection error handling. | None | ⬜ Not Started |

### UI/UX

| ID | Priority | Category | Task | Description | Dependencies | Status |
|---|---|---|---|---|---|---|
| UI-001 | 🟢 Low | UI/UX | Mobile Responsive Design | Add `sm:` breakpoints, convert sidebar to hamburger menu, responsive grid (`grid-cols-1 md:grid-cols-2 lg:grid-cols-3`), viewport meta tag, test on Chrome DevTools mobile emulator (375px). | None | ⬜ Not Started |

---

## Dependencies

```
DEP-001 depends on: API-001 (should fix API before deploying)
N8N-001 depends on: None (independent)
TEST-001 depends on: None (independent)
UI-001 depends on: None (independent)
```

---

## Execution Order

### Sprint 1 (Before Interview)

| Order | ID | Task | Est. Time |
|---|---|---|---|
| 1 | API-001 | Fix Knowledge page | 2-3 hours |
| 2 | TEST-001 | Ollama tests | 1-2 hours |
| 3 | DEP-001 | Deploy | 1 hour |

### Sprint 2 (Polish)

| Order | ID | Task | Est. Time |
|---|---|---|---|
| 4 | N8N-001 | n8n error handling | 2 hours |
| 5 | UI-001 | Mobile responsive | 2-3 hours |

---

## Definition of Done

| ID | DoD Criteria |
|---|---|
| API-001 | Knowledge page loads documents, can ingest, can delete — all without console errors |
| TEST-001 | 5+ new tests pass, `pytest tests/unit/test_ollama_provider.py` green |
| DEP-001 | Live URL accessible, health check returns 200 |
| N8N-001 | Each workflow has error branch + retry config |
| UI-001 | Dashboard usable at 375px width (iPhone SE) |