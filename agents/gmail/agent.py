# -*- coding: utf-8 -*-
"""Gmail Agent — dedicated Gmail API agent.

Capabilities: gmail.list, gmail.search, gmail.send, gmail.draft
"""
from __future__ import annotations

import asyncio
import base64
from email.message import EmailMessage
from typing import Any

from packages.config.settings import get_settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider

try:
    from integrations.google_client import get_google_credentials
    from googleapiclient.discovery import build as _gbuild

    _HAS_GOOGLE = True
except ImportError:
    _HAS_GOOGLE = False
    get_google_credentials = None  # type: ignore
    _gbuild = None  # type: ignore

SUPPORTED_ACTIONS = {"list", "search", "send", "draft"}


class GmailAgent:
    def __init__(self, descriptor: AgentDescriptor | None = None, llm: LLMProvider | None = None) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="gmail",
            domain=Domain.GMAIL,
            version="1",
            description="Gmail API: list, search, send, draft emails.",
            capabilities=frozenset({"gmail.list", "gmail.search", "gmail.send", "gmail.draft"}),
        )
        self._llm = llm or MockLLMProvider()

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message=f"unsupported action {request.action!r}"))
        settings = get_settings()
        # DRY-RUN handling for send/draft
        if request.action in ("send", "draft"):
            to_email = request.payload.get("to") or request.payload.get("to_email")
            subject = request.payload.get("subject", "")
            body = request.payload.get("body", "")
            if not to_email:
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.to required"))
            # If send disabled or no google creds -> draft mode / DRY-RUN
            dry = not settings.gmail_send_enabled or not _HAS_GOOGLE
            if dry:
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"mode": "DRY_RUN", "to": str(to_email), "subject": str(subject), "body": str(body)[:500], "draft": request.action == "draft"})
            try:
                creds = get_google_credentials()  # type: ignore
            except Exception as e:
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="CONFIG_ERROR", message=str(e)))
            return await self._send_gmail(request, creds, is_draft=(request.action == "draft"))
        # list/search require google
        if not _HAS_GOOGLE:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="CONFIG_ERROR", message="Gmail not configured"))
        try:
            creds = get_google_credentials()  # type: ignore
        except Exception as e:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="CONFIG_ERROR", message=str(e)))
        if request.action == "list":
            return await self._list(request, creds)
        if request.action == "search":
            return await self._search(request, creds)
        return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="UNKNOWN", message="unhandled"))

    async def _send_gmail(self, request: TaskRequest, creds: Any, is_draft: bool = False) -> AgentResponse:
        to_email = str(request.payload.get("to") or request.payload.get("to_email"))
        subject = str(request.payload.get("subject", ""))
        body = str(request.payload.get("body", ""))

        def _call() -> dict[str, Any]:
            svc = _gbuild("gmail", "v1", credentials=creds, cache_discovery=False)
            msg = EmailMessage()
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.set_content(body)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            if is_draft:
                return svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
            return svc.users().messages().send(userId="me", body={"raw": raw}).execute()

        try:
            res = await asyncio.to_thread(_call)
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"sent": not is_draft, "draft": is_draft, "response": res})
        except Exception as e:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="GMAIL_ERROR", message=str(e)))

    async def _list(self, request: TaskRequest, creds: Any) -> AgentResponse:
        max_results = int(request.payload.get("max_results", 10) or 10)

        def _call() -> list[dict[str, Any]]:
            svc = _gbuild("gmail", "v1", credentials=creds, cache_discovery=False)
            resp = svc.users().messages().list(userId="me", maxResults=max_results).execute()
            return resp.get("messages", [])

        try:
            msgs = await asyncio.to_thread(_call)
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"messages": msgs, "count": len(msgs)})
        except Exception as e:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="GMAIL_ERROR", message=str(e)))

    async def _search(self, request: TaskRequest, creds: Any) -> AgentResponse:
        query = str(request.payload.get("query") or request.payload.get("q") or "")
        if not query:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.query required"))
        max_results = int(request.payload.get("max_results", 10) or 10)

        def _call() -> list[dict[str, Any]]:
            svc = _gbuild("gmail", "v1", credentials=creds, cache_discovery=False)
            resp = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
            return resp.get("messages", [])

        try:
            msgs = await asyncio.to_thread(_call)
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"query": query, "messages": msgs, "count": len(msgs)})
        except Exception as e:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="GMAIL_ERROR", message=str(e)))


def create_gmail_agent(llm: LLMProvider | None = None) -> GmailAgent:
    return GmailAgent(llm=llm)
