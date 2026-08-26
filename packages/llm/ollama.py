"""Ollama provider — local LLM inference via Ollama API.

OPTIONAL (ADR-001): activated via `LLM_PROVIDER=ollama` + `LLM_MODEL=<model>`.
"""

from __future__ import annotations

import httpx
from typing import Any

from packages.config.settings import Settings
from packages.llm.base import T, provider_error
from packages.core.errors import LLMProviderError


class OllamaProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.ollama_base_url
        self.model = settings.llm_model or "qwen2.5:7b"
        self._timeout = httpx.Timeout(300.0, connect=5.0)

    @property
    def name(self) -> str:
        return "ollama"

    def _check_health(self) -> None:
        """Verify Ollama is reachable."""
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
        except Exception as e:
            raise provider_error(self.name, f"Ollama not reachable at {self._base_url}: {e}")

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate text completion from prompt."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": kwargs.get("temperature", 0.1),
                            "top_p": kwargs.get("top_p", 0.9),
                            "num_predict": kwargs.get("max_tokens", 2048),
                        },
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "")
            except httpx.HTTPStatusError as e:
                raise provider_error(self.name, f"Ollama API error {e.response.status_code}: {e.response.text}")
            except Exception as e:
                raise provider_error(self.name, f"Ollama request failed: {e}")

    async def generate_structured(
        self, prompt: str, schema: type[T], **kwargs: Any
    ) -> T:
        """Generate structured output matching schema (via JSON mode)."""
        import json

        schema_json = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
        structured_prompt = f"""{prompt}

Respond ONLY with valid JSON matching this schema:
{json.dumps(schema_json, ensure_ascii=False)}"""

        raw = await self.generate(structured_prompt, **kwargs)
        try:
            parsed = json.loads(raw)
            return schema.model_validate(parsed)
        except Exception as e:
            raise provider_error(self.name, f"Failed to parse structured output: {e}. Raw: {raw[:500]}")

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Chat completion with tool calling (Ollama doesn't support tools natively yet)."""
        # For now, flatten messages and generate with tool descriptions
        # Real tool calling would need a different approach (function calling models)
        tools_desc = "\n".join([f"- {t['function']['name']}: {t['function'].get('description', '')}" for t in tools])
        prompt = f"""You are a helpful assistant. Available tools:
{tools_desc}

Conversation:
{messages}

If you need to call a tool, respond with: TOOL_CALL: {{"name": "...", "arguments": {{...}}}}
Otherwise respond normally."""

        raw = await self.generate(prompt, **kwargs)

        # Parse for tool calls
        if "TOOL_CALL:" in raw:
            try:
                import json
                tool_part = raw.split("TOOL_CALL:")[1].strip()
                tool_call = json.loads(tool_part.split("\n")[0])
                return {"tool_calls": [tool_call], "content": None}
            except Exception:
                pass
        return {"content": raw, "tool_calls": []}

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """Generate embeddings via Ollama embeddings API."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                # Ollama embeddings endpoint: POST /api/embeddings
                resp = await client.post(
                    f"{self._base_url}/api/embeddings",
                    json={"model": self.model, "prompt": texts[0] if len(texts) == 1 else texts},
                )
                resp.raise_for_status()
                data = resp.json()
                # Handle both single and batch
                if "embedding" in data:
                    return [data["embedding"]]
                return data.get("embeddings", [])
            except Exception as e:
                raise provider_error(self.name, f"Ollama embeddings failed: {e}")