# -*- coding: utf-8 -*-
"""Business Ops Hub Agent (Task 2).

Aggregates three real operational sources into a single daily digest with
concrete "things to do" alerts:

* **Gmail** — unread messages (via ``gmail.list`` / ``gmail.search``).
* **Calendar** — upcoming events (via ``calendar.list_events``).
* **Tasks** — local operational to-dos (via an injected ``TaskProvider``).

The agent never fabricates data: every item in the digest comes from one of
the injected sources. The LLM (container LLM) is used only to *summarize* the
collected facts into a short Vietnamese intro line; if it is unavailable, a
deterministic fallback summary is used.

Capability: ``ops.digest`` (domain ``ops`` — ``Domain.OPS`` already exists).

Design for testability
----------------------
All three sources are injected, so the unit test passes mock callables /
:class:`~agents.ops_hub.tasks_provider.StaticTaskProvider` and asserts the
digest structure without any network or credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.llm.base import LLMProvider

from agents.ops_hub.tasks_provider import Task, TaskProvider

logger = logging.getLogger(__name__)

# Injected-source signatures.
GmailSource = Callable[[], Awaitable[list[dict[str, Any]]]]
CalendarSource = Callable[[], Awaitable[list[dict[str, Any]]]]


def _default_gmail_source() -> GmailSource:
    """Read Gmail unread via the gmail agent's ``list`` action."""

    async def _run() -> list[dict[str, Any]]:
        from packages.core.bootstrap import get_container
        from packages.contracts.models import TaskContext
        import uuid as _uuid

        ctn = get_container()
        desc, handler = ctn.registry.get_by_capability("gmail.list")
        resp = await handler.handle(
            TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.GMAIL,
                action="list",
                payload={"max_results": 50, "q": "is:unread"},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="ops_hub",
                ),
            )
        )
        if resp.status != AgentResponseStatus.SUCCESS or not resp.result:
            return []
        return list(resp.result.get("messages", []))

    return _run


def _default_calendar_source() -> CalendarSource:
    """Read upcoming calendar events via the calendar agent's ``list_events``."""

    async def _run() -> list[dict[str, Any]]:
        from packages.core.bootstrap import get_container
        from packages.contracts.models import TaskContext
        import uuid as _uuid

        ctn = get_container()
        desc, handler = ctn.registry.get_by_capability("calendar.list_events")
        resp = await handler.handle(
            TaskRequest(
                task_id=_uuid.uuid4(),
                domain=Domain.CALENDAR,
                action="list_events",
                payload={"max_results": 20},
                context=TaskContext(
                    organization_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    channel="ops_hub",
                ),
            )
        )
        if resp.status != AgentResponseStatus.SUCCESS or not resp.result:
            return []
        return list(resp.result.get("events", []))

    return _run


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DigestItem:
    kind: str  # "email" | "event" | "task"
    title: str
    detail: str
    due: datetime | None = None
    priority: str = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Digest:
    generated_at: datetime
    summary: str
    items: list[DigestItem]
    alerts: list[DigestItem]
    counts: dict[str, int]
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        def _ser(it: DigestItem) -> dict[str, Any]:
            return {
                "kind": it.kind,
                "title": it.title,
                "detail": it.detail,
                "due": it.due.isoformat() if it.due else None,
                "priority": it.priority,
                "metadata": it.metadata,
            }

        return {
            "generated_at": self.generated_at.isoformat(),
            "summary": self.summary,
            "items": [_ser(i) for i in self.items],
            "alerts": [_ser(a) for a in self.alerts],
            "counts": self.counts,
            "raw": self.raw,
        }


class OpsHubAgent:
    """Aggregates Gmail / Calendar / Tasks into a daily digest + alerts."""

    def __init__(
        self,
        *,
        gmail_source: GmailSource | None = None,
        calendar_source: CalendarSource | None = None,
        task_provider: TaskProvider | None = None,
        llm: LLMProvider | None = None,
        descriptor: AgentDescriptor | None = None,
        alert_window_hours: int = 24,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="ops_hub",
            domain=Domain.OPS,
            version="1",
            description=(
                "Business Ops Hub: aggregates Gmail unread, upcoming Calendar "
                "events and operational tasks into a daily digest with "
                "actionable alerts (ops.digest)."
            ),
            capabilities=frozenset({"ops.digest"}),
        )
        self._gmail = gmail_source or _default_gmail_source()
        self._calendar = calendar_source or _default_calendar_source()
        self._tasks = task_provider
        self._llm = llm
        self._alert_window_hours = alert_window_hours

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def build_digest(self, *, with_summary: bool = True) -> Digest:
        """Collect from all sources and assemble the digest + alerts."""
        now = _now()

        gmail_msgs = await self._safe(self._gmail, "gmail")
        calendar_evts = await self._safe(self._calendar, "calendar")
        tasks: list[Task] = []
        if self._tasks is not None:
            tasks = await self._safe(lambda: self._tasks.list_tasks(), "tasks") or []

        items: list[DigestItem] = []
        alerts: list[DigestItem] = []

        # --- Gmail unread -------------------------------------------------
        unread = [m for m in gmail_msgs if not _is_read(m)]
        for m in unread:
            items.append(
                DigestItem(
                    kind="email",
                    title=_email_subject(m) or "(không tiêu đề)",
                    detail=f"Từ: {_email_from(m) or '—'}",
                    priority="normal",
                    metadata={"id": m.get("id"), "snippet": (m.get("snippet") or "")[:200]},
                )
            )
        # Unread-but-important => alert (flagged or starred).
        for m in unread:
            if _email_flagged(m):
                alerts.append(
                    DigestItem(
                        kind="email",
                        title=_email_subject(m) or "(không tiêu đề)",
                        detail=f"⭐ Email chưa đọc quan trọng từ {_email_from(m) or '—'}",
                        priority="high",
                        metadata={"id": m.get("id")},
                    )
                )

        # --- Calendar upcoming -------------------------------------------
        for ev in calendar_evts:
            start = _event_start(ev)
            items.append(
                DigestItem(
                    kind="event",
                    title=_event_title(ev) or "(không tên)",
                    detail=_event_when(ev, start),
                    due=start,
                    priority="normal",
                    metadata={"id": ev.get("id")},
                )
            )
            if start is not None and 0 <= (start - now).total_seconds() <= self._alert_window_hours * 3600:
                alerts.append(
                    DigestItem(
                        kind="event",
                        title=_event_title(ev) or "(không tên)",
                        detail=f"⏰ Sự kiện sắp diễn ra: {_event_when(ev, start)}",
                        due=start,
                        priority="high",
                        metadata={"id": ev.get("id")},
                    )
                )

        # --- Tasks --------------------------------------------------------
        for t in tasks:
            items.append(
                DigestItem(
                    kind="task",
                    title=t.title,
                    detail=f"Ưu tiên: {t.priority}",
                    due=t.due,
                    priority=t.priority,
                    metadata={"id": t.id, "done": t.done},
                )
            )
            if t.due is not None and 0 <= (t.due - now).total_seconds() <= self._alert_window_hours * 3600:
                alerts.append(
                    DigestItem(
                        kind="task",
                        title=t.title,
                        detail=f"📌 Công việc đến hạn: {t.due.isoformat(timespec='minutes')}",
                        due=t.due,
                        priority=t.priority,
                        metadata={"id": t.id},
                    )
                )
            elif t.priority == "high":
                alerts.append(
                    DigestItem(
                        kind="task",
                        title=t.title,
                        detail="📌 Công việc ưu tiên cao cần xử lý",
                        due=t.due,
                        priority=t.priority,
                        metadata={"id": t.id},
                    )
                )

        counts = {
            "emails_unread": len(unread),
            "events_upcoming": len(calendar_evts),
            "tasks_open": len(tasks),
            "alerts": len(alerts),
        }

        summary = ""
        if with_summary:
            summary = await self._summarize(counts, alerts)

        return Digest(
            generated_at=now,
            summary=summary,
            items=items,
            alerts=alerts,
            counts=counts,
            raw={"emails": gmail_msgs, "events": calendar_evts, "tasks": [t.to_dict() for t in tasks]},
        )

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action != "digest":
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=f"ops_hub only supports action 'digest', got {request.action!r}",
                ),
            )
        try:
            digest = await self.build_digest(with_summary=True)
        except Exception as e:  # surface as a failed response, never fabricate
            logger.exception("ops.digest failed")
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="OPS_HUB_ERROR", message=str(e)),
            )
        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.SUCCESS,
            result=digest.to_dict(),
            metadata={"counts": digest.counts},
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _safe(self, coro_fn: Callable[[], Awaitable[Any]], label: str) -> Any:
        try:
            return await coro_fn()
        except Exception as e:
            logger.warning("ops_hub: nguồn %s lỗi, bỏ qua: %s", label, e)
            return [] if label != "tasks" else []

    async def _summarize(self, counts: dict[str, int], alerts: list[DigestItem]) -> str:
        n_email = counts.get("emails_unread", 0)
        n_event = counts.get("events_upcoming", 0)
        n_task = counts.get("tasks_open", 0)
        n_alert = counts.get("alerts", 0)
        base = (
            f"Bạn có {n_email} email chưa đọc, {n_event} sự kiện sắp tới, "
            f"{n_task} công việc cần làm. {n_alert} mục cần ưu tiên xử lý ngay."
        )
        if self._llm is None:
            return base
        try:
            out = await self._llm.generate(
                prompt=(
                    f"Tóm tắt ngắn gọn (tiếng Việt, dưới 2 câu) cho bản tin hiệu quả "
                    f"hàng ngày: {base}"
                ),
                system="Bạn là trợ lý Business Ops. Chỉ tóm tắt dữ liệu có thật, không bịa.",
                max_tokens=120,
                temperature=0.2,
            )
            return out.strip() if isinstance(out, str) and out.strip() else base
        except Exception as e:
            logger.warning("ops_hub summarize lỗi, dùng fallback: %s", e)
            return base


# ----------------------------------------------------------------------
# Source-record helpers (defensive — records come from external APIs)
# ----------------------------------------------------------------------
def _is_read(msg: dict[str, Any]) -> bool:
    return bool(msg.get("read") or msg.get("isRead"))


def _email_subject(msg: dict[str, Any]) -> str | None:
    return msg.get("subject") or (msg.get("payload", {}) or {}).get("subject")


def _email_from(msg: dict[str, Any]) -> str | None:
    return msg.get("from") or (msg.get("payload", {}) or {}).get("from")


def _email_flagged(msg: dict[str, Any]) -> bool:
    return bool(msg.get("flagged") or msg.get("starred") or msg.get("isImportant"))


def _event_start(ev: dict[str, Any]) -> datetime | None:
    raw = ev.get("start")
    if isinstance(raw, dict):
        raw = raw.get("dateTime") or raw.get("date")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
        # Calendar records may be naive locals; normalize to aware UTC so they
        # compare cleanly against _now() (UTC-aware) without a TypeError.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _event_title(ev: dict[str, Any]) -> str | None:
    return ev.get("summary") or ev.get("title")


def _event_when(ev: dict[str, Any], start: datetime | None) -> str:
    if start is not None:
        return start.isoformat(timespec="minutes")
    raw = ev.get("start")
    if isinstance(raw, dict):
        return str(raw.get("date") or raw.get("dateTime") or "")
    return str(raw or "")


def create_ops_hub_agent(
    *,
    gmail_source: GmailSource | None = None,
    calendar_source: CalendarSource | None = None,
    task_provider: TaskProvider | None = None,
    llm: LLMProvider | None = None,
    alert_window_hours: int = 24,
) -> OpsHubAgent:
    """Factory used by bootstrap / scripts (mirrors ``create_knowledge_agent``)."""
    return OpsHubAgent(
        gmail_source=gmail_source,
        calendar_source=calendar_source,
        task_provider=task_provider,
        llm=llm,
        alert_window_hours=alert_window_hours,
    )


__all__ = [
    "OpsHubAgent",
    "Digest",
    "DigestItem",
    "create_ops_hub_agent",
    "Task",
    "TaskProvider",
    "StaticTaskProvider",
    "InMemoryTaskProvider",
    "build_task_provider",
]
