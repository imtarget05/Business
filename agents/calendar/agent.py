"""Calendar Agent — Google Calendar integration.

Capabilities: calendar.list_events, calendar.create_event, calendar.delete_event
Gracefully degrades when Google credentials not configured.
"""

from __future__ import annotations

from typing import Any

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider

try:
    from googleapiclient.discovery import build as _gbuild

    from integrations.google_client import get_google_credentials

    _HAS_GOOGLE = True
except ImportError:
    _HAS_GOOGLE = False
    get_google_credentials = None  # type: ignore
    _gbuild = None  # type: ignore

SUPPORTED_ACTIONS = {"list_events", "create_event", "delete_event"}


class CalendarAgent:
    def __init__(
        self, descriptor: AgentDescriptor | None = None, llm: LLMProvider | None = None
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="calendar",
            domain=Domain.CALENDAR,
            version="1",
            description="Google Calendar integration: list, create, delete events.",
            capabilities=frozenset(
                {"calendar.list_events", "calendar.create_event", "calendar.delete_event"}
            ),
        )
        self._llm = llm or MockLLMProvider()

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR", message=f"unsupported action {request.action!r}"
                ),
            )
        if not _HAS_GOOGLE:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="CONFIG_ERROR",
                    message="Google Calendar not configured (missing google libraries)",
                ),
            )
        # Check credentials configured
        try:
            creds = get_google_credentials()  # type: ignore
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(code="CONFIG_ERROR", message=str(e)),
            )
        if request.action == "list_events":
            return await self._list_events(request, creds)
        if request.action == "create_event":
            return await self._create_event(request, creds)
        if request.action == "delete_event":
            return await self._delete_event(request, creds)
        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.FAILED,
            error=ErrorDetail(code="UNKNOWN", message="unhandled"),
        )

    async def _list_events(self, request: TaskRequest, creds: Any) -> AgentResponse:
        import asyncio

        calendar_id = str(request.payload.get("calendar_id", "primary"))
        max_results = int(request.payload.get("max_results", 10) or 10)

        def _call() -> list[dict[str, Any]]:
            svc = _gbuild("calendar", "v3", credentials=creds, cache_discovery=False)
            resp = (
                svc.events()
                .list(
                    calendarId=calendar_id,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            return resp.get("items", [])

        try:
            items = await asyncio.to_thread(_call)
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"events": items, "count": len(items)},
            )
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="CALENDAR_ERROR", message=str(e)),
            )

    async def _create_event(self, request: TaskRequest, creds: Any) -> AgentResponse:
        import asyncio

        summary = request.payload.get("summary") or request.payload.get("title")
        if not summary:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(code="VALIDATION_ERROR", message="payload.summary required"),
            )
        start = request.payload.get("start") or request.payload.get("start_time")
        end = request.payload.get("end") or request.payload.get("end_time")
        calendar_id = str(request.payload.get("calendar_id", "primary"))
        body: dict[str, Any] = {"summary": str(summary)}
        if start:
            body["start"] = {"dateTime": str(start)}
        if end:
            body["end"] = {"dateTime": str(end)}
        if request.payload.get("description"):
            body["description"] = str(request.payload["description"])

        def _call() -> dict[str, Any]:
            svc = _gbuild("calendar", "v3", credentials=creds, cache_discovery=False)
            return svc.events().insert(calendarId=calendar_id, body=body).execute()

        try:
            ev = await asyncio.to_thread(_call)
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"event": ev},
            )
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="CALENDAR_ERROR", message=str(e)),
            )

    async def _delete_event(self, request: TaskRequest, creds: Any) -> AgentResponse:
        import asyncio

        event_id = request.payload.get("event_id") or request.payload.get("id")
        if not event_id:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(code="VALIDATION_ERROR", message="payload.event_id required"),
            )
        calendar_id = str(request.payload.get("calendar_id", "primary"))

        def _call() -> None:
            svc = _gbuild("calendar", "v3", credentials=creds, cache_discovery=False)
            svc.events().delete(calendarId=calendar_id, eventId=str(event_id)).execute()

        try:
            await asyncio.to_thread(_call)
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"deleted": True, "event_id": str(event_id)},
            )
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="CALENDAR_ERROR", message=str(e)),
            )


def create_calendar_agent(llm: LLMProvider | None = None) -> CalendarAgent:
    return CalendarAgent(llm=llm)
