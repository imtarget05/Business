# -*- coding: utf-8 -*-
"""Youtube Agent — search, transcript, summarize.

Capabilities: youtube.search, youtube.transcript, youtube.summarize
"""
from __future__ import annotations

import re
from typing import Any

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider

try:
    from hermes_tools import web_search as _web_search, web_extract as _web_extract

    _HAS_HERMES = True
except ImportError:
    _HAS_HERMES = False
    _web_search = None  # type: ignore
    _web_extract = None  # type: ignore

SUPPORTED_ACTIONS = {"search", "transcript", "summarize"}


def _extract_video_id(url_or_id: str) -> str | None:
    s = url_or_id.strip()
    if re.match(r"^[A-Za-z0-9_-]{11}$", s):
        return s
    m = re.search(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else None


class YoutubeAgent:
    def __init__(self, descriptor: AgentDescriptor | None = None, llm: LLMProvider | None = None) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="youtube",
            domain=Domain.YOUTUBE,
            version="1",
            description="Youtube: search videos, get transcript, summarize.",
            capabilities=frozenset({"youtube.search", "youtube.transcript", "youtube.summarize"}),
        )
        self._llm = llm or MockLLMProvider()

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message=f"unsupported action {request.action!r}"))
        if request.action == "search":
            return await self._search(request)
        if request.action == "transcript":
            return await self._transcript(request)
        if request.action == "summarize":
            return await self._summarize(request)
        return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="UNKNOWN", message="unhandled"))

    async def _search(self, request: TaskRequest) -> AgentResponse:
        query = str(request.payload.get("query") or request.payload.get("q") or "").strip()
        if not query:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.query required"))
        limit = int(request.payload.get("limit", 5) or 5)
        if _HAS_HERMES and _web_search is not None:
            try:
                res = await _web_search(query=f"site:youtube.com {query}", limit=limit)  # type: ignore
                items = res.get("data", {}).get("web", []) if isinstance(res, dict) else []
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"query": query, "results": items, "count": len(items)})
            except Exception as e:
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="SEARCH_ERROR", message=str(e)))
        return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"query": query, "results": [{"title": f"mock youtube {query}", "url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}], "count": 1, "mock": True})

    async def _transcript(self, request: TaskRequest) -> AgentResponse:
        url = str(request.payload.get("url") or request.payload.get("video_id") or request.payload.get("video_url") or "").strip()
        if not url:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.url or payload.video_id required"))
        vid = _extract_video_id(url) or url
        # Try web_extract on youtube page
        if _HAS_HERMES and _web_extract is not None:
            try:
                yurl = f"https://www.youtube.com/watch?v={vid}" if len(vid) == 11 else url
                ex = await _web_extract(urls=[yurl])  # type: ignore
                content = ""
                if isinstance(ex, dict):
                    for r in ex.get("results", []):
                        content += r.get("content", "")[:5000]
                if content.strip():
                    return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"video_id": vid, "transcript": content[:8000]})
            except Exception as e:
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="EXTRACT_ERROR", message=str(e)))
        # Mock fallback
        return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"video_id": vid, "transcript": f"[mock transcript for {vid}]", "mock": True})

    async def _summarize(self, request: TaskRequest) -> AgentResponse:
        text = str(request.payload.get("text") or request.payload.get("transcript") or "").strip()
        url = str(request.payload.get("url") or request.payload.get("video_id") or "").strip()
        if not text and not url:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.text or payload.url required"))
        if not text and url:
            # fetch transcript first
            tmp = await self._transcript(TaskRequest(task_id=request.task_id, action="transcript", payload={"url": url}, context=request.context))
            if tmp.status == AgentResponseStatus.SUCCESS:
                text = str(tmp.result.get("transcript", "")) if tmp.result else ""
            else:
                return tmp
        try:
            summary = await self._llm.generate(prompt=f"Summarize this youtube transcript concisely:\n{text[:7000]}", system="You are a youtube summarizer.")
            s = summary if isinstance(summary, str) else str(summary)
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"summary": s})
        except Exception as e:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="LLM_ERROR", message=str(e)))


def create_youtube_agent(llm: LLMProvider | None = None) -> YoutubeAgent:
    return YoutubeAgent(llm=llm)
