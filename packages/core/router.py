"""Router Agent (Phase 4) — intent classification over free-form text.

Distinction from registry routing (see docs/architecture/router-vs-registry.md):
registry routing resolves a caller-specified capability string; the Router
Agent *infers* the intent (domain + action) from raw text via LLM structured
classification with a deterministic rule-based fallback and an escalation
path for low confidence.

Closed intent set aligned to the pilot use case (customer-support email).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from packages.llm.base import LLMProvider


class _AgentRegistryProtocol(Protocol):
    """Minimal registry interface for routing table construction."""

    def list_agents(self) -> list: ...


def _build_routing_table(registry: _AgentRegistryProtocol | None) -> frozenset[tuple[str, str]]:
    """Build routing table from registry's advertised capabilities.

    Extracts (domain, action) pairs from all registered agents' capabilities.
    Capabilities follow the format "domain.action" (e.g., "knowledge.query").
    """
    if registry is None:
        # Fallback to hardcoded intents for backward compatibility / standalone use
        return frozenset(
            {
                ("support", "triage"),
                ("support", "draft_reply"),
                ("knowledge", "query"),
            }
        )

    intents: set[tuple[str, str]] = set()
    for descriptor in registry.list_agents():
        for cap in descriptor.capabilities:
            # Capabilities are validated to be "domain.action" format
            domain, _, action = cap.partition(".")
            if domain and action:
                intents.add((domain, action))
    return frozenset(intents)


# Keyword rules — checked in order; first match wins. Deterministic fallback
# used when the LLM is unavailable or its answer is not in ROUTER_INTENTS.
RULE_FALLBACKS: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (("hoàn tiền", "refund", "trả hàng", "return"), ("support", "triage")),
    (("khiếu nại", "complaint", "không hoạt động", "broken", "lỗi"), ("support", "triage")),
    (
        ("chính sách", "policy", "bao nhiêu", "bảo hành", "warranty",
         "đổi trả", "faq", "ship", "vận chuyển"),
        ("knowledge", "query"),
    ),
)

# Keyword -> capability tokens (capability-based matching, ADR-012).
# Each entry: (keywords, required-capability-fragments). A fragment is matched
# against "domain.action" strings of registered agents; agents covering more
# fragments score higher. This lets the router pick among *candidates* rather
# than only accepting/rejecting one exact intent.
CAPABILITY_KEYWORDS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("pod", "kubernetes", "kubectl", "deploy", "rollback"), ("ops", "kubernetes")),
    (("log", "logs", "logging"), ("ops", "logs")),
    (("metric", "monitor", "alert", "uptime"), ("ops", "monitoring")),
    (("root cause", "why", "crash", "incident", "outage"), ("ops", "root_cause")),
    (("tồn kho", "inventory", "stock", "nhập hàng"), ("supply_chain",)),
    (("đơn hàng", "purchase order", "po ", "po#", "nhà cung cấp"), ("supply_chain",)),
    (("email", "hộp thư", "inbox", "gmail"), ("gmail",)),
    (("lịch", "calendar", "meeting", "cuộc họp"), ("calendar",)),
    (("video", "youtube", "kênh"), ("youtube",)),
    (("nghiên cứu", "research", "arxiv", "tin tức", "search"), ("research",)),
    (("chính sách", "policy", "faq", "câu hỏi thường gặp"), ("knowledge",)),
    (("khiếu nại", "refund", "hoàn tiền", "trả hàng"), ("support",)),
    (("báo cáo", "report", "dashboard", "thống kê"), ("report",)),
    (("tổng hợp", "ops", "vận hành", "cần làm", "digest", "công việc"), ("ops",)),
    (("báo giá", "quote", "proposal", "đề xuất", "chào giá", "email khách", "báo gia"), ("sales",)),
    (("đối thủ", "competitor", "cạnh tranh", "competitive", "giá đối thủ", "doi thu", "doi thu"), ("competitor",)),
)


def score_candidates(
    text: str,
    registry: _AgentRegistryProtocol | None,
) -> list[tuple[str, float]]:
    """Score registered agents by capability fit for free text.

    Returns [(qualified_name, score)] sorted desc; only agents with score > 0.
    Deterministic (keyword overlap), zero LLM cost — used as the candidate
    stage before LLM classification / rule fallback / escalation.
    """
    if registry is None:
        return []
    lowered = text.lower()
    wanted: list[tuple[str, ...]] = []
    for keywords, fragments in CAPABILITY_KEYWORDS:
        if any(k in lowered for k in keywords):
            wanted.extend(fragments)

    scored: list[tuple[str, float]] = []
    for descriptor in registry.list_agents():
        score = 0.0
        for cap in descriptor.capabilities:
            for fragment in wanted:
                if fragment in cap:
                    score += 1.0
        # domain mention bonus
        if descriptor.domain.value and descriptor.domain.value in lowered:
            score += 0.5
        if score > 0:
            scored.append((descriptor.qualified_name, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


DEFAULT_CONFIDENCE_THRESHOLD = 0.6



class _LLMClassification(BaseModel):
    domain: str = Field(min_length=1)
    action: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class Classification:
    domain: str | None
    action: str | None
    confidence: float
    escalate: bool
    source: str  # "llm" | "rules" | "escalated"

    @property
    def capability(self) -> str | None:
        if self.domain is None or self.action is None:
            return None
        return f"{self.domain}.{self.action}"


class RouterAgent:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        registry: _AgentRegistryProtocol | None = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._llm = llm
        self._threshold = confidence_threshold
        self._routing_table = _build_routing_table(registry)
        self._registry = registry

    def candidates(self, text: str) -> list[tuple[str, float]]:
        """Capability-scored candidate agents for free text (ADR-012)."""
        return score_candidates(text, self._registry)

    def set_dynamic_rules(self, rules: list) -> None:
        """Inject learned routing rules [(keyword, capability), ...] (ADR-010)."""
        self._dynamic_rules = list(rules or [])

    def _dynamic_rule_match(self, text: str) -> tuple[str, str] | None:
        for rule in getattr(self, "_dynamic_rules", []):
            keyword, capability = rule[0], rule[1]
            if keyword.lower() in text.lower():
                return tuple(capability.split(".", 1))  # type: ignore[return-value]
        return None



    def _get_allowed_intents(self) -> frozenset[tuple[str, str]]:
        """Return the current routing table (intents the router can classify to)."""
        return self._routing_table

    async def classify_text(self, text: str) -> Classification:
        text = text.strip()
        if not text:
            return Classification(None, None, 0.0, True, "escalated")

        allowed_intents = self._get_allowed_intents()

        # 1. Try LLM structured classification.
        try:
            raw = await self._llm.generate_structured(
                _build_prompt(text, allowed_intents),
                schema=_LLMClassification,
                system=_build_system_prompt(allowed_intents),
                temperature=0.0,
                max_tokens=64,
            )
            pair = (raw.domain, raw.action)
            if pair in allowed_intents and raw.confidence >= self._threshold:
                return Classification(raw.domain, raw.action, raw.confidence, False, "llm")
            if pair in allowed_intents:
                # Recognized intent but weak confidence -> try rules, else escalate.
                rule = self._rule_match(text)
                if rule is not None:
                    return Classification(*rule, 0.5, False, "rules")
                return Classification(raw.domain, raw.action, raw.confidence, True, "escalated")
            # Hallucinated intent outside the closed set -> never route to it.
        except Exception:
            pass  # fall through to deterministic rules

        # 2. Learned dynamic rules (ADR-010) then rule-based fallback.
        rule = self._dynamic_rule_match(text) or self._rule_match(text)
        if rule is not None:
            return Classification(*rule, 0.5, False, "rules")

        # 3. Escalate rather than guess.
        return Classification(None, None, 0.0, True, "escalated")

    def _rule_match(self, text: str) -> tuple[str, str] | None:
        lowered = text.lower()
        for keywords, intent in RULE_FALLBACKS:
            if any(k in lowered for k in keywords):
                return intent
        return None


def _build_system_prompt(allowed_intents: frozenset[tuple[str, str]]) -> str:
    intents_str = ", ".join(sorted(f"{d}.{a}" for d, a in allowed_intents))
    return (
        "You classify inbound customer messages into exactly one "
        f"of these intents: {intents_str}. "
        "Return low confidence when unsure."
    )


def _build_prompt(text: str, allowed_intents: frozenset[tuple[str, str]]) -> str:
    intents_str = ", ".join(sorted(f"{d}.{a}" for d, a in allowed_intents))
    return (
        "Classify this customer message into one of the allowed intents.\n"
        f"ALLOWED_INTENTS: {intents_str}\n"
        f"MESSAGE:\n{text[:2000]}"
    )


# Backward compatibility exports
ROUTER_INTENTS = frozenset(
    {
        ("support", "triage"),
        ("support", "draft_reply"),
        ("knowledge", "query"),
    }
)


__all__ = [
    "RouterAgent",
    "Classification",
    "ROUTER_INTENTS",
    "RULE_FALLBACKS",
    "CAPABILITY_KEYWORDS",
    "score_candidates",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "_build_routing_table",
]