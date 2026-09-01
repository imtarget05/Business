# CV Claims — Interview-Defensible Only

> Brutally honest assessment of what you can say in an interview without getting caught.

---

## 1. Safe to Claim

These are verifiable, unambiguous, and will survive scrutiny.

| Claim | Evidence |
|-------|----------|
| Built a multi-agent system with 15 functional agents | All 15 agents have working logic; no stubs or placeholders |
| Implemented PII masking for emails, 10-digit VN phones, and 12-digit CCCD | Verified working in tests |
| Achieved 885+ passing tests with CI green on GitHub Actions | CI pipeline confirmed passing |
| Defined n8n workflows that can be imported and run | Workflows are importable and executable |
| Implemented Telegram bot integration | Code exists; needs real token to activate |
| Implemented Gmail API integration | Code exists; needs OAuth credentials to activate |
| Created a RAG evaluation pipeline with Recall@5 and MRR metrics | Pipeline exists and produces results |
| Documented 11 ADRs for architectural decisions | ADRs exist and are version-controlled |
| Implemented audit logging that classifies operations as READ/WRITE/DESTRUCTIVE | Logging is functional and queryable |
| Built cost tracking infrastructure (LLM usage logging + Grafana dashboards) | Infrastructure defined; awaiting production data |

---

## 2. Claim with Caveats

True, but you must add the context or you'll look like you're overselling.

| Claim | Required Caveat |
|-------|-----------------|
| "PII masking works" | Only catches clean formats — fails on phone/CCCD with spaces, 11-digit landlines, and 16-digit cards are blocked before masking |
| "RAG Recall@5 = 1.000, MRR = 0.746" | Only 17 documents and 12 queries with 1 relevant doc each — trivially achievable, not statistically significant |
| "9 ADRs accurately reflect the architecture" | 2 ADRs are contradicted by actual implementation (ADR-002 default is SQLite not Neon; ADR-003 violated by AI logic in n8n) |
| "Audit layer enforces security" | It only classifies and logs — no behavioral enforcement based on risk level |
| "Cost tracking in production" | 45 entries exist but all are test data; $0 real spend; Grafana dashboards are empty |
| "Telegram integration" | Bot is implemented but untested with a real token |
| "Gmail integration" | API integration exists but untested with real OAuth credentials |
| "Slack integration" | Webhook-only via n8n — no native Slack SDK integration |

---

## 3. Do NOT Claim

These will get you caught. Avoid entirely or reframe completely.

| Dangerous Claim | Why It Fails |
|-----------------|--------------|
| "15 agents in production" | No production deployment; all test data |
| "PII masking is comprehensive" | Misses spaced formats, landlines, card numbers |
| "RAG system is high-performing" | Results are from a toy dataset; would be laughed at without context |
| "Architecture follows ADRs" | 2/11 ADRs are violated by the actual implementation |
| "Security audit layer protects data" | It's metadata logging, not enforcement |
| "Cost-optimized LLM usage" | No production cost data exists |
| "Fully tested integrations" | Telegram and Gmail are untested with real credentials |
| "Production-ready system" | No production deployment, no real spend, no real users |

---

## 4. Interview Soundbites

Prepared answers to common questions. Keep it to 2-3 seconds.

**Q: How many agents did you build?**
"I built 15 functional agents — each with working logic, no stubs. The system handles document processing, customer ops, and internal workflows."

**Q: How do you handle PII?**
"We mask emails, phone numbers, and national IDs. It works on clean formats — we're aware it misses spaced variants and are iterating on that."

**Q: What's your RAG performance like?**
"We built an evaluation pipeline measuring Recall@5 and MRR. On our current dataset we hit 1.0 and 0.746 respectively — but it's a small dataset, so we treat those as directional, not definitive."

**Q: Is this in production?**
"It's a working prototype with CI green and 885+ tests. Integrations are implemented but awaiting real credentials for full end-to-end testing."

**Q: How do you track costs?**
"We log every LLM call to a structured file and have Grafana dashboards defined. Right now it's all test data — we'll have real numbers once we go live."

---

*Last updated: 2026-09-01*
