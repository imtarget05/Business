"""LLM provider abstraction (STEP 0.7 / ADR-005).

Business logic MUST call `LLMProvider` only — never a concrete SDK such as
`ollama.generate(...)`.
"""

from packages.llm.base import LLMProvider
from packages.llm.factory import get_llm_provider
from packages.llm.mock import MockLLMProvider

__all__ = ["LLMProvider", "MockLLMProvider", "get_llm_provider"]
