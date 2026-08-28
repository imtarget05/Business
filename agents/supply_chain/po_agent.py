"""Purchase Order Agent — inbound PO parsing, classification, routing, and policy check.

This agent implements the DomainAgent protocol. It receives raw PO email content
(or any text blob), extracts structured PO data, classifies the PO type (new /
reorder / exchange), and routes it based on policy thresholds defined in settings.

Provider fallback is handled at call time: if the LLM provider throws any
intermittent error (timeout, rate-limit, connection refused), the agent falls
back to rule-based parsing/classification so the workflow continues without
stopping (per stored memory: LLM provider fallback policy).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from packages.config.settings import Settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskRequest,
    TaskContext,
)
from packages.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class POItem:
    sku: str
    description: str
    quantity: int
    unit_price: float
    total_price: float


@dataclass
class PurchaseOrder:
    po_number: str
    vendor: str
    vendor_email: str | None = None
    date: str | None = None
    items: list[POItem] = field(default_factory=list)
    total: float = 0.0
    po_type: str = "unknown"
    route: str = "auto_approved"


# ---------------------------------------------------------------------------
# PO Agent
# ---------------------------------------------------------------------------

SUPPLY_CHAIN_CAPABILITIES = frozenset(
    {"supply_chain.parse_po", "supply_chain.classify_po", "supply_chain.route_po"}
)

SUPPORTED_ACTIONS = frozenset({"parse_po", "classify_po", "route_po", "process_po"})


class PurchaseOrderAgent:
    """Agent xử lý PO inbound: parse email → structured PO → classify → route."""

    def __init__(self, llm: LLMProvider | None = None, settings: Settings | None = None) -> None:
        self.descriptor = AgentDescriptor(
            name="purchase_order_agent",
            domain=Domain.SUPPLY_CHAIN,
            version="1",
            description="Parse inbound purchase orders, classify PO type, and route based on policy thresholds.",
            capabilities=SUPPLY_CHAIN_CAPABILITIES,
            timeout_ms=30_000,
            max_retries=2,
        )
        self._llm = llm
        self._settings = settings

    SUPPORTED_ACTIONS = frozenset({"parse_po", "classify_po", "route_po", "process_po"})

    @property
    def llm(self) -> LLMProvider | None:
        return self._llm

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"unsupported action: {request.action!r}",
                ),
            )

        content = request.payload.get("email_content")
        if not content or not isinstance(content, str):
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="cannot parse PO: missing or non-string email_content",
                ),
            )

        po = await self._parse_po(content, request)
        if po is None:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="PARSE_ERROR",
                    message="failed to parse purchase order from email content",
                ),
            )

        po_type = await self._classify_po(po, request)
        if po_type is None:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="CLASSIFY_ERROR",
                    message="failed to classify PO type",
                ),
            )

        po.po_type = po_type
        route = await self._route_po(po, request)
        if route is None:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(
                    code="ROUTING_ERROR",
                    message="failed to route purchase order",
                ),
            )

        po.route = route

        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result={
                "po": {
                    "po_number": po.po_number,
                    "vendor": po.vendor,
                    "vendor_email": po.vendor_email,
                    "date": po.date,
                    "items": [
                        {
                            "sku": item.sku,
                            "description": item.description,
                            "quantity": item.quantity,
                            "unit_price": item.unit_price,
                            "total_price": item.total_price,
                        }
                        for item in po.items
                    ],
                    "total": po.total,
                    "po_type": po.po_type,
                    "route": po.route,
                },
                "status": "processed",
            },
        )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    async def _parse_po(self, text: str, request: TaskRequest) -> PurchaseOrder | None:
        po = await self._llm_parse_po(text, request)
        if po is not None:
            return po

        logger.debug("LLM parse returned None, falling back to rule-based parser")
        return self._rule_parse_po(text)

    async def _llm_parse_po(self, text: str, request: TaskRequest) -> PurchaseOrder | None:
        """LLM-based PO parsing with structured output."""
        if self._llm is None:
            return None

        try:
            result = await self._llm.generate_structured(
                prompt=(
                    "Extract purchase order information from the following email/text.\n"
                    "Return a JSON object with these fields:\n"
                    "  - po_number: string (e.g. PO-2024-001)\n"
                    "  - vendor: string (vendor/supplier name)\n"
                    "  - vendor_email: string or null\n"
                    "  - date: string YYYY-MM-DD or null\n"
                    "  - items: array of {sku, description, quantity, unit_price, total_price}\n"
                    "  - total: number\n\n"
                    f"Text to parse:\n{text}"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "po_number": {"type": "string"},
                        "vendor": {"type": "string"},
                        "vendor_email": {"type": "string"},
                        "date": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sku": {"type": "string"},
                                    "description": {"type": "string"},
                                    "quantity": {"type": "integer"},
                                    "unit_price": {"type": "number"},
                                    "total_price": {"type": "number"},
                                },
                                "required": ["sku", "description", "quantity", "unit_price", "total_price"],
                            },
                        },
                        "total": {"type": "number"},
                    },
                    "required": ["po_number", "vendor", "items", "total"],
                },
                request=request,
            )

            if result is None:
                return None

            po_data = result

            items = []
            for item_data in po_data.get("items", []):
                items.append(
                    POItem(
                        sku=item_data.get("sku", ""),
                        description=item_data.get("description", ""),
                        quantity=item_data.get("quantity", 0),
                        unit_price=item_data.get("unit_price", 0.0),
                        total_price=item_data.get("total_price", 0.0),
                    )
                )

            po = PurchaseOrder(
                po_number=po_data.get("po_number", "UNKNOWN"),
                vendor=po_data.get("vendor", "Unknown Vendor"),
                vendor_email=po_data.get("vendor_email"),
                date=po_data.get("date"),
                items=items,
                total=po_data.get("total", 0.0),
            )

            logger.debug(
                "LLM parsed PO: %s, vendor=%s, items=%d, total=%.2f",
                po.po_number,
                po.vendor,
                len(po.items),
                po.total,
            )
            return po

        except Exception as exc:
            logger.warning("LLM parse failed for PO extraction: %s", exc)
            return None

    def _rule_parse_po(self, text: str) -> PurchaseOrder | None:
        """Rule-based PO parsing fallback using regex."""
        lines = text.splitlines()

        po_number = "UNKNOWN"
        vendor = "Unknown Vendor"
        vendor_email = None
        date = None
        items: list[POItem] = []
        total = 0.0

        # PO number patterns — try the most specific format first
        candidates = [
            r"\b(PO-\d{4}-\w+(?:-\w+)*)\b",   # PO-2024-FALLBACK-TEST, PO-2024-001
            r"\b(PO[-]?\s*\d+(?:[-]?\w+)*)\b", # PO 2024, PO-2024-001
            r"\b(PO\s*\d{4,})\b",              # PO 2024...
        ]
        for pat in candidates:
            m = re.search(pat, text)
            if m:
                po_number = m.group(1).strip()
                break

        # Vendor
        m = re.search(r"(?:VENDOR|From|Supplier):\s*(.+)", text, re.IGNORECASE)
        if m:
            vendor = m.group(1).strip()

        # Vendor email
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
        if m:
            vendor_email = m.group(0)

        # Date
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if m:
            date = m.group(1)

        # Items: lines starting with "-" or "N." (e.g. "- SKU-001, Widget, QTY: 10 @ $5.00 = $50.00")
        # Capture SKU and the full remainder after the first comma.
        item_lines = re.findall(
            r"^\s*(?:[-*]|\d+\.\s)\s*([A-Z0-9-]+)\s*,\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        for sku, remainder in item_lines:
            qty_match = re.search(r"(\d+)\s*units?", remainder, re.IGNORECASE)
            qty = int(qty_match.group(1)) if qty_match else 0

            price_match = re.search(r"\$(\d+\.?\d*)\s*each", remainder, re.IGNORECASE)
            unit_price = float(price_match.group(1)) if price_match else 0.0

            total_match = re.search(r"=\s*\$(\d+\.?\d*)\s*total", remainder, re.IGNORECASE)
            line_total = float(total_match.group(1)) if total_match else 0.0

            # Description: everything before the QTY/price portion
            desc_match = re.match(r"(.+?)(?:\s*,\s*QTY:|\s*-\s*\d+\s*units)", remainder, re.IGNORECASE)
            desc = desc_match.group(1).strip() if desc_match else remainder.strip()

            items.append(
                POItem(
                    sku=sku.strip(),
                    description=desc or sku.strip(),
                    quantity=qty,
                    unit_price=unit_price,
                    total_price=line_total,
                )
            )

        # Total amount
        m = re.search(r"TOTAL[:\s]*\$(\d+\.?\d*)", text, re.IGNORECASE)
        if m:
            total = float(m.group(1))

        if not items and not total:
            return None

        return PurchaseOrder(
            po_number=po_number,
            vendor=vendor,
            vendor_email=vendor_email,
            date=date,
            items=items,
            total=total,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    async def _classify_po(self, po: PurchaseOrder, request: TaskRequest) -> str | None:
        if self._llm is not None:
            result = await self._llm_classify(po, request)
            if result is not None:
                return result

        return self._rule_classify_po(po)

    async def _llm_classify(self, po: PurchaseOrder, request: TaskRequest) -> str | None:
        """LLM-based classification."""
        if self._llm is None:
            return None

        try:
            items_desc = "; ".join([it.description for it in po.items]) if po.items else ""
            prompt = (
                f"Classify this purchase order into one of: new, reorder, exchange.\n\n"
                f"Vendor: {po.vendor}\n"
                f"Items: {items_desc}\n"
                f"PO Number: {po.po_number}\n\n"
                f"Respond with ONLY one word: new, reorder, or exchange."
            )

            result = await self._llm.generate(
                prompt=prompt,
                request=request,
            )

            if result is None:
                return None

            text = (result or "").strip().lower()
            for candidate in ("new", "reorder", "exchange"):
                if candidate in text:
                    logger.debug("LLM classified PO %s as %s", po.po_number, candidate)
                    return candidate

            return None

        except Exception as exc:
            logger.warning("LLM classify failed: %s", exc)
            return None

    def _rule_classify_po(self, po: PurchaseOrder) -> str:
        """Rule-based classification fallback."""
        text = f"{po.vendor} {'; '.join([it.description for it in po.items])} {po.po_number}"

        if re.search(r"\b(re-?order|restock|repeat|recurring)\b", text, re.IGNORECASE):
            return "reorder"

        if re.search(r"\b(ex-?change|swap|return|replace|correction)\b", text, re.IGNORECASE):
            return "exchange"

        return "new"

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _route_po(self, po: PurchaseOrder, request: TaskRequest) -> str | None:
        thresholds = self._settings.po_approval_thresholds if self._settings else {}
        manager_a = thresholds.get("manager_a", 500.0)
        manager_b = thresholds.get("manager_b", 5000.0)

        total = po.total

        if total > manager_b:
            return "approval_required_manager_b"
        elif total > manager_a:
            return "approval_required_manager_a"
        else:
            return "auto_approved"
