# ADR-001: No Local LLM Requirement

## Context

Development machines are constrained (~2 GB RAM spare). Running local models
via Ollama would consume gigabytes of RAM/disk, slow onboarding, and make the
system unusable on the target hardware. The platform must also run in CI and
free-tier production where local inference is impractical.

## Decision

- The application **never requires a local LLM**.
- Ollama exists only as an **optional** `LLMProvider` implementation
  (`OllamaProvider`), disabled unless explicitly configured.
- The default provider is `MockLLMProvider` — deterministic, zero-credential,
  zero-network. Tests and CI always use it.
- No model files are committed or downloaded by Docker images.

## Alternatives considered

1. **Require Ollama for dev** — rejected: violates the hardware constraint and
   breaks CI portability.
2. **Embed a small model in the repo** — rejected: bloats repository, still
   needs local inference resources, complicates licensing.
3. **Cloud-only (no mock)** — rejected: tests would need network + API keys,
   making them flaky and non-free.

## Consequences

- ✅ `pytest`, CI and `docker compose up` work with zero credentials.
- ✅ Provider swap is configuration-only (`LLM_PROVIDER=...`).
- ⚠️ Real generation quality depends on configuring a cloud provider; the mock
  must never be mistaken for a working NLU pipeline.
- ⚠️ `OllamaProvider` ships as a skeleton and is validated only when someone
  actually runs Ollama.
