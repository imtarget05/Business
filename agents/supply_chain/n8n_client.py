"""n8n webhook client (Phase D — Task 3.5).

Exports approved Purchase Orders to an n8n workflow via HTTP POST. The n8n
webhook URL is configured via environment (N8N_WEBHOOK_URL) or settings; when
unset the client degrades gracefully to a no-op (log + cached payload) so the
supply chain graph never hard-fails on a missing integration.

Failures are captured and returned as a result dict — callers decide whether to
surface them. This keeps the n8n step non-blocking for the core PO flow.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class N8nResult:
    exported: bool
    webhook_url: str | None
    status_code: int | None = None
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class N8nClient:
    """Minimal async client for posting approved POs to an n8n webhook."""

    def __init__(
        self,
        webhook_url: str | None = None,
        *,
        timeout_seconds: float = 10.0,
        enabled: bool | None = None,
    ) -> None:
        self._webhook_url = webhook_url or os.environ.get("N8N_WEBHOOK_URL")
        self._timeout = timeout_seconds
        # Explicit flag overrides; otherwise enabled iff a URL is configured.
        self._enabled = enabled if enabled is not None else bool(self._webhook_url)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def webhook_url(self) -> str | None:
        return self._webhook_url

    async def export_po(self, po_payload: dict[str, Any]) -> N8nResult:
        """POST an approved PO to the configured n8n webhook.

        Returns N8nResult with exported=False (and no error) when disabled, so
        the supply chain graph treats the step as a successful no-op.
        """
        if not self._enabled or not self._webhook_url:
            logger.info("n8n export skipped (disabled / no webhook URL)")
            return N8nResult(exported=False, webhook_url=self._webhook_url, payload=po_payload)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._webhook_url, json=po_payload)
            if resp.status_code >= 400:
                logger.warning(f"n8n webhook returned {resp.status_code}: {resp.text[:200]}")
                return N8nResult(
                    exported=False,
                    webhook_url=self._webhook_url,
                    status_code=resp.status_code,
                    error=f"HTTP {resp.status_code}",
                    payload=po_payload,
                )
            logger.info(f"n8n export OK ({resp.status_code})")
            return N8nResult(
                exported=True,
                webhook_url=self._webhook_url,
                status_code=resp.status_code,
                payload=po_payload,
            )
        except Exception as exc:  # network / timeout / DNS
            logger.warning(f"n8n export failed: {exc}")
            return N8nResult(
                exported=False,
                webhook_url=self._webhook_url,
                error=str(exc),
                payload=po_payload,
            )


__all__ = ["N8nClient", "N8nResult"]
