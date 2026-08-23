# ADR-005: LLM Provider Abstraction

## Context

The platform must support multiple inference backends over time: Cloudflare
Workers AI (free-tier friendly), any OpenAI-compatible endpoint, optional local
Ollama, and a deterministic mock for tests. Business logic must not be coupled
to any vendor SDK.

## Decision

All LLM access goes through the `LLMProvider` protocol in `packages/llm`:

- `generate(prompt, *, system, temperature, max_tokens) -> str`
- `generate_structured(prompt, schema: type[T], ...) -> T` (validated Pydantic)

Implementations: `MockLLMProvider` (works in Phase 0), `CloudflareAIProvider`,
`ExternalOpenAICompatibleProvider`, `OllamaProvider` (skeletons until
credentials exist). Selection happens in the factory via typed settings —
never in business code.

Rules:

1. Business/orchestrator code may only import from `packages.llm.base`.
2. Direct SDK calls (`ollama.generate(...)`, etc.) are forbidden outside a
   provider implementation.
3. Provider failures surface as the unified `LLMProviderError`.

## Alternatives considered

1. **LangChain** — rejected for Phase 0: heavyweight abstraction we don't
   control; our needs are two methods.
2. **LiteLLM proxy** — viable later as an *ExternalOpenAICompatible* target;
   not a core dependency.
3. **Per-vendor code paths in agents** — rejected outright (vendor lock-in).

## Consequences

- ✅ Provider swap is one env var; tests are deterministic with mock.
- ✅ Vendor-specific details isolated to `packages/llm/<provider>.py`.
- ⚠️ Structured-output quality varies by provider; each implementation must
  handle schema validation/retry itself before returning.
