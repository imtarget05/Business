"""Research Agent — web search & arxiv.

Capabilities: research.web_search, research.summarize, research.arxiv_search
Web access via ``packages.tools`` (ADR-008): hermes optional, httpx fallback.
"""

from __future__ import annotations

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider
from packages.tools.web import WebToolsProvider, create_web_tools

SUPPORTED_ACTIONS = {"web_search", "summarize", "arxiv_search"}


class ResearchAgent:
    def __init__(
        self,
        descriptor: AgentDescriptor | None = None,
        llm: LLMProvider | None = None,
        web_tools: WebToolsProvider | None = None,
    ) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="research",
            domain=Domain.RESEARCH,
            version="1",
            description="Web research: web search, arxiv search, summarize.",
            capabilities=frozenset(
                {"research.web_search", "research.summarize", "research.arxiv_search"}
            ),
        )
        self._llm = llm or MockLLMProvider()
        self._web = web_tools or create_web_tools("auto")

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
        if request.action == "web_search":
            return await self._web_search(request)
        if request.action == "arxiv_search":
            return await self._arxiv_search(request)
        if request.action == "summarize":
            return await self._summarize(request)
        return AgentResponse(
            task_id=request.task_id,
            agent=self.descriptor.qualified_name,
            status=AgentResponseStatus.FAILED,
            error=ErrorDetail(code="UNKNOWN", message="unhandled"),
        )

    async def _web_search(self, request: TaskRequest) -> AgentResponse:
        query = str(request.payload.get("query") or request.payload.get("q") or "").strip()
        if not query:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(code="VALIDATION_ERROR", message="payload.query required"),
            )
        limit = int(request.payload.get("limit", 5) or 5)
        try:
            res = await self._web.web_search(query=query, limit=limit)
            items = res.get("data", {}).get("web", []) if isinstance(res, dict) else []
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"query": query, "results": items, "count": len(items)},
            )
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="SEARCH_ERROR", message=str(e)),
            )

    async def _arxiv_search(self, request: TaskRequest) -> AgentResponse:
        query = str(request.payload.get("query") or request.payload.get("q") or "").strip()
        if not query:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(code="VALIDATION_ERROR", message="payload.query required"),
            )
        # Use web_search with arxiv site filter
        try:
            res = await self._web.web_search(query=f"site:arxiv.org {query}", limit=5)
            items = res.get("data", {}).get("web", []) if isinstance(res, dict) else []
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"query": query, "results": items, "count": len(items)},
            )
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="SEARCH_ERROR", message=str(e)),
            )

    async def _summarize(self, request: TaskRequest) -> AgentResponse:
        text = str(request.payload.get("text") or request.payload.get("content") or "").strip()
        urls = request.payload.get("urls") or []
        if not text and not urls:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.REJECTED,
                error=ErrorDetail(
                    code="VALIDATION_ERROR", message="payload.text or payload.urls required"
                ),
            )
        # If urls provided, extract content first (best effort)
        if urls:
            try:
                ex = await self._web.web_extract(urls=list(urls)[:3])
                # ex is dict with results
                if isinstance(ex, dict):
                    extracted = " ".join(r.get("content", "")[:2000] for r in ex.get("results", []))
                    if extracted and "mock content" not in extracted:
                        text = extracted
            except Exception:
                pass
        if not text:
            text = " ".join(str(u) for u in urls)
        try:
            summary = await self._llm.generate(
                prompt=f"Summarize concisely:\n{text[:6000]}",
                system="You are a research summarizer. Be concise and factual.",
            )
            s = summary if isinstance(summary, str) else str(summary)
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.SUCCESS,
                result={"summary": s},
            )
        except Exception as e:
            return AgentResponse(
                task_id=request.task_id,
                agent=self.descriptor.qualified_name,
                status=AgentResponseStatus.FAILED,
                error=ErrorDetail(code="LLM_ERROR", message=str(e)),
            )


def create_research_agent(
    llm: LLMProvider | None = None, web_tools: WebToolsProvider | None = None
) -> ResearchAgent:
    return ResearchAgent(llm=llm, web_tools=web_tools)
