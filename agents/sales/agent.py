"""Email-to-Proposal Automation Agent (Task 4).

Reads a customer email, classifies its intent, generates a branded proposal
(template from ``data/templates/proposal.md`` + pricing from
``data/templates/pricing.json``), renders a PDF with company branding via
**reportlab** (pure-python, offline — Ruling: reportlab, not weasyprint), and
drafts a Vietnamese follow-up email.

Capability: ``sales.process_email`` (domain ``sales`` — ``Domain.SALES``).

Design for testability
----------------------
The whole pipeline (classify -> proposal -> price -> follow-up -> PDF) is
**deterministic and needs no network or LLM**. An LLM can be injected to
refine client/scope extraction, but process_email() works fully offline so the
unit test asserts the proposal structure, price, and follow-up without any
mock model. ``render_pdf`` produces real, non-empty PDF bytes from reportlab.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskRequest,
)
from packages.llm.base import LLMProvider

# --------------------------------------------------------------------------- #
# Paths (resolved relative to this file so tests run from repo root)
# --------------------------------------------------------------------------- #
_AGENTS_SALES_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AGENTS_SALES_DIR.parent.parent
DATA_DIR = _REPO_ROOT / "data"
TEMPLATE_PATH = DATA_DIR / "templates" / "proposal.md"
PRICING_PATH = DATA_DIR / "templates" / "pricing.json"
BRAND_PATH = DATA_DIR / "brand" / "brand.json"

# Intent classification keywords (VN + EN). First match wins.
_INTENT_KEYWORDS: tuple[tuple[str, ...], str] = (
    (
        "khiếu nại",
        "khieu nai",
        "complaint",
        "phàn nàn",
        "phan nan",
        "hoàn tiền",
        "hoan tien",
        "refund",
        "không hài lòng",
        "khong hai long",
        "tệ",
        "toi te",
        "bad service",
    ),
    "complaint",
)
_QUOTE_KEYWORDS: tuple[tuple[str, ...], str] = (
    (
        "báo giá",
        "bao gia",
        "quote",
        "báo cáo giá",
        "baogia",
        "price",
        "chi phí",
        "chi phi",
        "phí",
        "fee",
        "cost",
        "giá",
        "gia",
    ),
    "quote_request",
)
_SERVICE_KEYWORDS: tuple[tuple[str, ...], str] = (
    (
        "dịch vụ",
        "dich vu",
        "service",
        "ra mắt",
        "ra mat",
        "launch",
        "tư vấn",
        "tu van",
        "consult",
        "giúp",
        "giup",
        "giới thiệu",
        "gioi thieu",
        "offer",
        "package",
        "gói",
        "goi",
        "proposal",
        "đề xuất",
        "de xuat",
    ),
    "service_inquiry",
)

# Package selection keywords (lowercased on match).
_PACKAGE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "launch_impact",
        ("launch", "ra mắt", "ra mat", "impact", "ra mắt thương hiệu", "launch impact"),
    ),
    (
        "growth_boost",
        (
            "growth",
            "tăng trưởng",
            "tang truong",
            "tối ưu",
            "toi uu",
            "optimize",
            "quảng cáo",
            "quang cao",
        ),
    ),
    (
        "starter",
        ("starter", "cơ bản", "co ban", "basic", "khởi đầu", "khoi dau"),
    ),
]


def _load_json(path: Path) -> dict[str, Any]:
    import json

    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_brand(path: Path | None = None) -> dict[str, Any]:
    """Load branding config (placeholder logo path is fine — no file needed)."""
    return _load_json(path or BRAND_PATH)


def load_pricing(path: Path | None = None) -> dict[str, Any]:
    return _load_json(path or PRICING_PATH)


# --------------------------------------------------------------------------- #
# Result models
# --------------------------------------------------------------------------- #
class ProposalResult(BaseModel):
    intent: str
    client: str
    package_key: str
    proposal_name: str
    scope: str
    timeline: str
    price: float
    currency: str
    proposal_markdown: str
    follow_up: dict[str, str] = Field(default_factory=dict)
    brand: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Intent + extraction helpers (deterministic)
# --------------------------------------------------------------------------- #
def classify_intent(text: str) -> str:
    low = text.lower()
    for keywords, intent in (_INTENT_KEYWORDS, _QUOTE_KEYWORDS, _SERVICE_KEYWORDS):
        if any(k in low for k in keywords):
            return intent
    return "other"


def _extract_client(text: str, brand: dict[str, Any]) -> str:
    """Best-effort client name from email body (signature / 'from').

    Falls back to a polite generic label — never fabricates a real person.
    """
    # Common Vietnamese/English signature markers.
    sig_match = re.search(
        r"(?:từ|from|trân trọng|best regards|thanks?)[^\n]*\n\s*([A-ZÀ-Ỹ][A-Za-zà-ỹ]*(?:\s+[A-ZÀ-Ỹ][A-Za-zà-ỹ]*){0,3})",
        text,
        re.IGNORECASE,
    )
    if sig_match:
        return sig_match.group(1).strip()
    # A line that looks like a name (2-3 capitalized words, no email/url).
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if (
            2 <= len(line.split()) <= 3
            and line[0:1].isalpha()
            and "@" not in line
            and "." not in line
        ):
            if any(c.isupper() for c in line):
                return line
    return "Quý khách hàng"


def _select_package(text: str, pricing: dict[str, Any]) -> str:
    low = text.lower()
    for key, keywords in _PACKAGE_KEYWORDS:
        if any(k in low for k in keywords):
            return key
    return str(pricing.get("default_package", "launch_impact"))


def _render_template(
    template: str,
    *,
    brand: dict[str, Any],
    client: str,
    proposal_name: str,
    scope: str,
    timeline: str,
    price: float,
    currency: str,
    support_duration: str,
    validity_days: int,
) -> str:
    return (
        template.replace("{{company_name}}", str(brand.get("company_name", "Our Company")))
        .replace("{{client}}", client)
        .replace("{{date}}", date.today().isoformat())
        .replace("{{brand_company}}", str(brand.get("company_name", "Our Company")))
        .replace("{{proposal_name}}", proposal_name)
        .replace("{{scope}}", scope)
        .replace("{{timeline}}", timeline)
        .replace(
            "{{pricing_table}}",
            f"- Gói: **{proposal_name}**\n- Đơn giá: **{price:,.0f} {currency}**",
        )
        .replace("{{price}}", f"{price:,.0f}")
        .replace("{{currency}}", currency)
        .replace("{{support_duration}}", support_duration)
        .replace("{{validity_days}}", str(validity_days))
        .replace("{{brand_tagline}}", str(brand.get("tagline", "")))
    )


# --------------------------------------------------------------------------- #
# PDF rendering (reportlab — offline, pure python)
# --------------------------------------------------------------------------- #
def _find_font() -> str:
    """Return a Vietnamese-capable font name if available, else 'Helvetica'."""
    candidates = [
        r"C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for cand in candidates:
        if Path(cand).exists():
            return cand
    return ""


def render_pdf(proposal: dict[str, Any] | ProposalResult, brand: dict[str, Any]) -> bytes:
    """Render a branded proposal PDF and return raw bytes.

    Uses reportlab Platypus. Falls back to Helvetica if no Vietnamese TTF is
    found (still produces valid PDF bytes; diacritics may be dropped).
    """
    from reportlab.lib.colors import Color, HexColor
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    if isinstance(proposal, ProposalResult):
        proposal = proposal.model_dump()

    font_path = _find_font()
    if font_path:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        _name = "BrandFont"
        if _name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(_name, font_path))
        font_name = _name
        base_font = _name
    else:
        font_name = "Helvetica"
        base_font = "Helvetica"

    primary = brand.get("primary_color", "#1E88E5")
    accent = brand.get("accent_color", "#FFC107")
    try:
        primary_color = HexColor(primary)
        accent_color = HexColor(accent)
    except Exception:
        primary_color = Color(0.118, 0.533, 0.898)
        accent_color = Color(1.0, 0.757, 0.027)

    company = str(brand.get("company_name", "Our Company"))
    tagline = str(brand.get("tagline", ""))
    contact = str(brand.get("contact_email", ""))
    phone = str(brand.get("contact_phone", ""))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BrandTitle",
        parent=styles["Title"],
        fontName=font_name,
        textColor=primary_color,
        fontSize=20,
    )
    sub_style = ParagraphStyle(
        "BrandSub",
        parent=styles["Normal"],
        fontName=font_name,
        textColor=accent_color,
        fontSize=11,
        alignment=TA_CENTER,
    )
    h_style = ParagraphStyle(
        "BrandH",
        parent=styles["Heading2"],
        fontName=font_name,
        textColor=primary_color,
        fontSize=13,
    )
    body_style = ParagraphStyle(
        "BrandBody", parent=styles["Normal"], fontName=base_font, fontSize=10.5, leading=15
    )
    footer_style = ParagraphStyle(
        "BrandFooter",
        parent=styles["Normal"],
        fontName=base_font,
        fontSize=8,
        textColor=accent_color,
        alignment=TA_CENTER,
    )

    def _esc(s: Any) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story: list[Any] = []
    story.append(Paragraph(_esc(company), title_style))
    if tagline:
        story.append(Paragraph(_esc(tagline), sub_style))
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=primary_color))
    story.append(Spacer(1, 0.4 * cm))

    client = proposal.get("client", "Quý khách hàng")
    proposal_name = proposal.get("proposal_name", "")
    story.append(Paragraph(_esc(f"BÁO GIÁ & ĐỀ XUẤT — {proposal_name}"), h_style))
    story.append(Paragraph(_esc(f"Gửi: {client}"), body_style))
    story.append(Paragraph(_esc(f"Ngày: {date.today().isoformat()}"), body_style))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("Phạm vi công việc (Scope)", h_style))
    story.append(Paragraph(_esc(proposal.get("scope", "")), body_style))
    story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("Thời gian thực hiện (Timeline)", h_style))
    story.append(Paragraph(_esc(proposal.get("timeline", "")), body_style))
    story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("Báo giá (Pricing)", h_style))
    price = proposal.get("price", 0)
    currency = proposal.get("currency", "VND")
    story.append(Paragraph(_esc(f"Tổng giá trị hợp đồng: {price:,.0f} {currency}"), body_style))
    story.append(Spacer(1, 0.3 * cm))

    story.append(HRFlowable(width="100%", thickness=1, color=accent_color))
    contact_line = company
    if contact:
        contact_line += f"  •  {contact}"
    if phone:
        contact_line += f"  •  {phone}"
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(_esc(contact_line), footer_style))

    # Build into an in-memory buffer.
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"Proposal — {proposal_name}",
        author=company,
    )
    doc.build(story)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
class SalesAgent:
    """Email-to-Proposal automation: email -> proposal + PDF + follow-up."""

    def __init__(
        self,
        *,
        llm: LLMProvider | None = None,
        descriptor: AgentDescriptor | None = None,
        template_path: Path | None = None,
        pricing_path: Path | None = None,
        brand_path: Path | None = None,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="sales",
            domain=Domain.SALES,
            version="1",
            description=(
                "Email-to-Proposal Automation: reads a customer email, classifies "
                "intent, generates a branded proposal + pricing, renders a PDF "
                "(reportlab, offline) and drafts a Vietnamese follow-up email "
                "(sales.process_email)."
            ),
            capabilities=frozenset({"sales.process_email"}),
        )
        self._llm = llm
        self._template_path = template_path or TEMPLATE_PATH
        self._pricing_path = pricing_path or PRICING_PATH
        self._brand_path = brand_path or BRAND_PATH

    # ------------------------------------------------------------------ #
    # Public pipeline
    # ------------------------------------------------------------------ #
    def process_email(
        self,
        email_text: str,
        *,
        brand: dict[str, Any] | None = None,
        client: str | None = None,
        package_key: str | None = None,
    ) -> ProposalResult:
        """Deterministic email -> proposal + pricing + follow-up (no network)."""
        brand = brand or load_brand(self._brand_path)
        pricing = load_pricing(self._pricing_path)

        intent = classify_intent(email_text)
        resolved_client = client or _extract_client(email_text, brand)
        resolved_package = package_key or _select_package(email_text, pricing)

        pkg = (pricing.get("packages") or {}).get(
            resolved_package,
            (pricing.get("packages") or {}).get(pricing.get("default_package", "launch_impact")),
        )
        if not pkg:
            # Defensive: never fabricate — use a clearly-empty placeholder.
            pkg = {
                "label": resolved_package,
                "price": 0,
                "scope": "(chưa có thông tin gói)",
                "timeline": "(chưa xác định)",
                "support_duration": "—",
            }

        currency = str(pricing.get("currency", "VND"))
        validity_days = int(pricing.get("validity_days", 14))
        proposal_name = str(pkg.get("label", resolved_package))
        scope = str(pkg.get("scope", ""))
        timeline = str(pkg.get("timeline", ""))
        support_duration = str(pkg.get("support_duration", "—"))
        price = float(pkg.get("price", 0))

        proposal_md = _render_template(
            _load_text(self._template_path),
            brand=brand,
            client=resolved_client,
            proposal_name=proposal_name,
            scope=scope,
            timeline=timeline,
            price=price,
            currency=currency,
            support_duration=support_duration,
            validity_days=validity_days,
        )

        follow_up = self._draft_follow_up(
            intent=intent,
            client=resolved_client,
            proposal_name=proposal_name,
            brand=brand,
        )

        return ProposalResult(
            intent=intent,
            client=resolved_client,
            package_key=resolved_package,
            proposal_name=proposal_name,
            scope=scope,
            timeline=timeline,
            price=price,
            currency=currency,
            proposal_markdown=proposal_md,
            follow_up=follow_up,
            brand=brand,
        )

    def _draft_follow_up(
        self, *, intent: str, client: str, proposal_name: str, brand: dict[str, Any]
    ) -> dict[str, str]:
        company = str(brand.get("company_name", "Our Company"))
        contact = str(brand.get("contact_email", ""))
        if intent == "complaint":
            subject = f"Tiếp nhận khiếu nại từ {client} — {company}"
            body = (
                f"Kính gửi {client},\n\n"
                f"Cảm ơn bạn đã phản hồi. Chúng tôi rất tiếc về trải nghiệm chưa tốt "
                f"và đã tiếp nhận khiếu nại của bạn. Bộ phận CSKH sẽ liên hệ lại trong "
                f"vòng 24h để xử lý tận gốc.\n\n"
                f"Trân trọng,\n{company}" + (f"\n{contact}" if contact else "")
            )
        else:
            subject = f"Báo giá & đề xuất {proposal_name} dành cho {client} — {company}"
            body = (
                f"Kính gửi {client},\n\n"
                f"Cảm ơn bạn đã quan tâm đến dịch vụ của {company}. Theo yêu cầu trong "
                f'email của bạn, mình gửi kèm đề xuất "{proposal_name}" (file PDF).\n\n'
                f"Nếu bạn cần điều chỉnh phạm vi hoặc muốn đặt lịch cuộc gọi 15 phút để "
                f"chốt phương án, phản hồi email này nhé.\n\n"
                f"Trân trọng,\n{company}" + (f"\n{contact}" if contact else "")
            )
        return {"subject": subject, "body": body}

    def render_pdf(
        self, proposal: dict[str, Any] | ProposalResult, brand: dict[str, Any] | None = None
    ) -> bytes:
        return render_pdf(proposal, brand or load_brand(self._brand_path))

    # ------------------------------------------------------------------ #
    # Capability handler
    # ------------------------------------------------------------------ #
    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action != "process_email":
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"sales only supports action 'process_email', got {request.action!r}",
                ),
            )

        email_text = str(
            request.payload.get("email_text") or request.payload.get("email") or ""
        ).strip()
        if not email_text:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message="payload.email_text (or payload.email) is required for sales.process_email",
                ),
            )

        client = request.payload.get("client")
        package_key = request.payload.get("package_key")
        brand_override = request.payload.get("brand")

        try:
            result = self.process_email(
                email_text,
                brand=brand_override,
                client=client,
                package_key=package_key,
            )
            pdf_bytes = self.render_pdf(result, result.brand)
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={
                    **result.model_dump(),
                    "pdf_bytes": pdf_bytes,
                    "pdf_size": len(pdf_bytes),
                },
                confidence=0.9,
                metadata={"intent": result.intent, "package_key": result.package_key},
            )
        except Exception as e:  # surface, never fabricate
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="SALES_ERROR", message=str(e)),
            )

    async def process_email_async(
        self, email_text: str, *, brand: dict[str, Any] | None = None, client: str | None = None
    ) -> ProposalResult:
        """Async convenience wrapper (mirrors other agents' public API)."""
        return self.process_email(email_text, brand=brand, client=client)

    async def render_pdf_async(
        self, proposal: dict[str, Any] | ProposalResult, brand: dict[str, Any] | None = None
    ) -> bytes:
        return self.render_pdf(proposal, brand)


def create_sales_agent(
    *,
    llm: LLMProvider | None = None,
    template_path: Path | None = None,
    pricing_path: Path | None = None,
    brand_path: Path | None = None,
) -> SalesAgent:
    """Factory used by bootstrap / scripts (mirrors other agents)."""
    return SalesAgent(
        llm=llm,
        template_path=template_path,
        pricing_path=pricing_path,
        brand_path=brand_path,
    )


__all__ = [
    "SalesAgent",
    "ProposalResult",
    "create_sales_agent",
    "render_pdf",
    "load_brand",
    "load_pricing",
    "classify_intent",
    "TEMPLATE_PATH",
    "PRICING_PATH",
    "BRAND_PATH",
]
