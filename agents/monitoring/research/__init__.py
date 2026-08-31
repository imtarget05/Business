# -*- coding: utf-8 -*-
"""Research agents — multi-agent research workflow with LangGraph.

Provides:
- ResearchAgentBase: state machine for research workflow
- WebSearchAgent: web search via web_search tool
- ArxivAgent: arxiv paper search

State flow:
    INIT → SEARCH → EXTRACT → SUMMARIZE → REPORT → END
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver

from packages.tools.web import create_web_tools

# Module-level provider, resolved once (hermes -> httpx -> mock via ADR-008).
# Tests monkeypatch _call_web_search / _call_web_extract directly.
_tools = create_web_tools("auto")


# Wrappers (monkeypatchable in tests) — never raise for missing hermes;
# the factory already degraded to httpx or mock.
# Ambiguous tech terms that dictionary sites hijack in search results
# (e.g. "agent là gì" returns "agent = người đại diện" from dictionaries).
# When one of these appears in the query, add "AI" context to the search.
_AMBIGUOUS_TECH_TERMS = (
    "multi-agent", "agent", "llm", "rag", "token", "prompt", "embedding",
    "transformer", "inference", "fine-tuning", "copilot", "chatbot",
    "machine learning", "deep learning",
)


def _enrich_search_query(query: str) -> str:
    """Add AI/tech context to search queries containing ambiguous tech terms."""
    q = query.strip()
    if not q:
        return q
    low = q.lower()
    words = set(re.findall(r"[a-z][a-z\-]+", low))
    if "ai" in words or "artificial intelligence" in low:
        return q
    for term in _AMBIGUOUS_TECH_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", low):
            return f"{q} AI"
    return q


async def _call_web_search(query: str, limit: int = 5) -> dict[str, Any]:
    return await _tools.web_search(query=_enrich_search_query(query), limit=limit)


async def _call_web_extract(urls: list[str], char_limit: int = 5000) -> dict[str, Any]:
    return await _tools.web_extract(urls=urls[:3], char_limit=char_limit)


def _looks_like_html(text: str) -> bool:
    """Heuristic: True if the text is raw HTML rather than readable content."""
    if not text:
        return False
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or "<script" in head[:500]


def _looks_like_error_text(text: str) -> bool:
    """True if the 'content' is actually an extract/HTTP error message, not article text."""
    if not text:
        return False
    low = text.lower()
    return (
        "[extract error" in low
        or "client error" in low
        or "forbidden" in low
        or "403" in low[:60]
    )





# ---------------------------------------------------------------------------
# Research State
# ---------------------------------------------------------------------------

@dataclass
class ResearchState:
    """State carried through research workflow."""
    
    task_id: UUID
    query: str = ""
    domain: str = "research"  # "web", "arxiv", or "general"
    
    # Search results
    search_results: list[dict[str, Any]] = field(default_factory=list)
    
    # Extracted content
    extracted_content: list[dict[str, Any]] = field(default_factory=list)
    
    # Summary
    summary: str = ""
    
    # Final report
    report: str = ""
    
    # Flow control
    current_step: str = "init"
    error: str | None = None
    step_history: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False
    final_result: dict[str, Any] | None = None


def _record_step(state: ResearchState, step: str, status: str) -> None:
    state.step_history.append({
        "step": step,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
    })


# ---------------------------------------------------------------------------
# Base Research Agent
# ---------------------------------------------------------------------------

class ResearchAgentBase:
    """Base class for research agents.
    
    Subclasses implement search() and extract() methods.
    """
    
    def __init__(self, domain: str = "general") -> None:
        self.domain = domain
    
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search for information. Override in subclasses."""
        raise NotImplementedError
    
    async def extract(self, results: list[dict[str, Any]], char_limit: int = 5000) -> list[dict[str, Any]]:
        """Extract content from search results. Override in subclasses."""
        raise NotImplementedError
    
    async def summarize(self, extracted: list[dict[str, Any]], query: str) -> str:
        """Synthesize a clean answer from extracted content.

        Uses the local LLM when available (qwen2.5); falls back to a clean
        concatenation of non-empty snippets. If every source was blocked/unreadable,
        says so plainly instead of dumping raw HTML.
        """
        clean = [e for e in extracted if e.get("content")]
        if not clean:
            return (
                f"Không thu thập được nội dung hữu ích cho '{query}'. "
                "Các nguồn được tìm thấy bị chặn (403/blocked) hoặc không trả về "
                "văn bản. Hãy thử từ khóa khác hoặc nguồn chính thức."
            )

        # Try LLM synthesis (local, free) when a provider is configured.
        try:
            from packages.llm import get_llm_provider
            from packages.config.settings import get_settings

            llm = get_llm_provider(get_settings())

            has_real_info = any(len(e["content"].strip()) > 15 for e in clean)
            if has_real_info:
                ctx = "\n\n".join(
                    f"[{e['title']}] {e['content'][:1500]}" for e in clean[:5]
                )
                system = (
                    "Bạn là trợ lý nghiên cứu của một trợ lý kinh doanh công nghệ (AI/tech). "
                    "Chỉ dùng thông tin trong nguồn, không bịa. "
                    "Nếu thuật ngữ trong câu hỏi có nhiều nghĩa, ưu tiên nghĩa trong "
                    "công nghệ thông tin/AI (ví dụ 'agent' = tác tử AI, KHÔNG phải "
                    "'người đại diện' trong từ điển). "
                    "Tổng hợp ý chính từ các nguồn thay vì sao chép nguyên văn. "
                    "Trả lời ngắn gọn, 3-5 gạch đầu dòng, tiếng Việt."
                )
                prompt = (
                    f"Câu hỏi: {query}\n\n"
                    f"Nguồn tìm thấy trên web:\n{ctx}\n\n"
                    "Nếu các nguồn trên chỉ là định nghĩa từ điển thông thường (không "
                    "liên quan công nghệ/AI), hãy trả lời theo nghĩa công nghệ/AI dựa "
                    "trên hiểu biết của bạn và ghi rõ điều đó."
                )
            else:
                # Web sources were blocked / returned empty snippets -> answer from
                # the model's own knowledge instead of echoing the (useless) snippets.
                system = (
                    "Bạn là trợ lý nghiên cứu. Dùng kiến thức của bạn, ngắn gọn, "
                    "tiếng Việt, không bịa."
                )
                prompt = f"Trả lời ngắn gọn (tiếng Việt) cho câu hỏi: {query}"

            answer = await llm.generate(prompt, system=system, temperature=0.2)
            if answer and len(answer.strip()) > 30 and not answer.lstrip().startswith("["):
                return answer.strip()
        except Exception as e:
            # LLM unavailable -> fall through to clean concatenation
            logger.warning("research summarize LLM failed: %s", e)

        # Fallback: clean concatenation (no raw HTML reaches here)
        parts = [f"**{e['title']}**: {e['content'][:400]}" for e in clean]
        return "\n\n".join(parts) if parts else f"No content extracted for query: {query}"
    
    async def generate_report(self, summary: str, query: str, domain: str) -> str:
        """Generate final report."""
        return f"""# Research Report: {query}

**Domain**: {domain}
**Generated**: {datetime.now(timezone.utc).isoformat()}

## Summary

{summary}

---
*Generated by Research Agent ({self.__class__.__name__})*
"""


# ---------------------------------------------------------------------------
# Web Search Agent
# ---------------------------------------------------------------------------

class WebSearchAgent(ResearchAgentBase):
    """Web search research agent using web_search tool."""
    
    def __init__(self) -> None:
        super().__init__(domain="web")
    
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search web for query."""
        result = await _call_web_search(query=query, limit=limit)
        return result.get("data", {}).get("web", [])
    
    async def extract(self, results: list[dict[str, Any]], char_limit: int = 5000) -> list[dict[str, Any]]:
        """Extract content from web results, discarding blocked/raw-HTML entries."""
        urls = [r.get("url") for r in results if r.get("url")]

        if not urls:
            # No URLs (e.g. search snippets only): keep any non-HTML description.
            out = []
            for r in results:
                desc = r.get("snippet") or r.get("description") or r.get("content") or ""
                if not _looks_like_html(desc):
                    out.append({
                        "title": r.get("title", ""),
                        "content": desc,
                        "url": r.get("url", ""),
                    })
            return out

        # Extract top 3 results
        extracted = []
        extract_result = await _call_web_extract(urls=urls[:3], char_limit=char_limit)
        results_data = extract_result.get("results", [])

        for r in results_data:
            if r.get("error"):
                # 403 / blocked / extract failure -> skip, do not dump into report
                continue
            content = r.get("content") or ""
            if _looks_like_html(content):
                # Raw HTML returned instead of article text -> skip
                continue
            if _looks_like_error_text(content):
                # web_extract returned "[extract error: 403 ...]" as content -> skip
                continue
            if not content and r.get("title"):
                content = r.get("title")
            extracted.append({
                "title": r.get("title", ""),
                "content": content,
                "url": r.get("url", ""),
                "error": None,
            })

        if not extracted:
            # Every web extract failed/blocked (403 etc.) -> fall back to the
            # search snippets (snippet/description) so the report still has real
            # content to synthesize from instead of coming back empty.
            for r in results:
                desc = r.get("snippet") or r.get("description") or ""
                if desc and not _looks_like_html(desc) and not _looks_like_error_text(desc):
                    extracted.append({
                        "title": r.get("title", ""),
                        "content": desc,
                        "url": r.get("url", ""),
                        "error": None,
                    })

        return extracted


# ---------------------------------------------------------------------------
# Arxiv Agent
# ---------------------------------------------------------------------------

class ArxivAgent(ResearchAgentBase):
    """Arxiv paper search research agent."""
    
    def __init__(self) -> None:
        super().__init__(domain="arxiv")
    
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search arxiv for papers."""
        from arxiv import Search, SortCriterion
        
        search = Search(
            query=query,
            max_results=limit,
            sort_by=SortCriterion.Relevance,
        )
        
        results = []
        async for paper in search.results():
            results.append({
                "title": paper.title,
                "authors": ", ".join(author.name for author in paper.authors),
                "summary": paper.summary[:1000] if paper.summary else "",
                "url": paper.pdf_url,
                "published": paper.published.isoformat() if paper.published else "",
                "categories": ", ".join(paper.categories),
            })
        
        return results
    
    async def extract(self, results: list[dict[str, Any]], char_limit: int = 5000) -> list[dict[str, Any]]:
        """Extract content from arxiv results (already have summary)."""
        # Arxiv results already contain summary, just return them
        return results


# ---------------------------------------------------------------------------
# Research Nodes (LangGraph)
# ---------------------------------------------------------------------------

async def search_node(state: ResearchState) -> ResearchState:
    """Execute search based on domain."""
    _record_step(state, "search", "started")
    
    try:
        if state.domain == "web":
            agent = WebSearchAgent()
        elif state.domain == "arxiv":
            agent = ArxivAgent()
        else:
            agent = WebSearchAgent()  # default
        
        results = await agent.search(state.query, limit=5)
        state.search_results = results
        state.current_step = "search_done"
        _record_step(state, "search", "success")
    except Exception as e:
        state.error = f"Search error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "search", "failed")
    
    return state


async def extract_node(state: ResearchState) -> ResearchState:
    """Extract content from search results."""
    _record_step(state, "extract", "started")
    
    if state.error:
        return state
    
    try:
        if state.domain == "web":
            agent = WebSearchAgent()
        elif state.domain == "arxiv":
            agent = ArxivAgent()
        else:
            agent = WebSearchAgent()
        
        extracted = await agent.extract(state.search_results)
        state.extracted_content = extracted
        state.current_step = "extract_done"
        _record_step(state, "extract", "success")
    except Exception as e:
        state.error = f"Extract error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "extract", "failed")
    
    return state


async def summarize_node(state: ResearchState) -> ResearchState:
    """Generate summary from extracted content."""
    _record_step(state, "summarize", "started")
    
    if state.error:
        return state
    
    try:
        if state.domain == "web":
            agent = WebSearchAgent()
        elif state.domain == "arxiv":
            agent = ArxivAgent()
        else:
            agent = WebSearchAgent()
        
        summary = await agent.summarize(state.extracted_content, state.query)
        state.summary = summary
        state.current_step = "summarize_done"
        _record_step(state, "summarize", "success")
    except Exception as e:
        state.error = f"Summarize error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "summarize", "failed")
    
    return state


async def report_node(state: ResearchState) -> ResearchState:
    """Generate final report."""
    _record_step(state, "report", "started")
    
    if state.error:
        return state
    
    try:
        if state.domain == "web":
            agent = WebSearchAgent()
        elif state.domain == "arxiv":
            agent = ArxivAgent()
        else:
            agent = WebSearchAgent()
        
        report = await agent.generate_report(state.summary, state.query, state.domain)
        state.report = report
        state.current_step = "report_done"
        
        state.final_result = {
            "status": "success",
            "query": state.query,
            "domain": state.domain,
            "summary": state.summary,
            "report": state.report,
            "search_results_count": len(state.search_results),
            "extracted_count": len(state.extracted_content),
        }
        state.terminal = True
        _record_step(state, "report", "success")
    except Exception as e:
        state.error = f"Report error: {str(e)}"
        state.current_step = "end"
        state.terminal = True
        state.final_result = {"status": "failed", "error": state.error}
        _record_step(state, "report", "failed")
    
    return state


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------

def after_search(state: ResearchState) -> str:
    if state.error:
        return "error"
    return "extract"


def after_extract(state: ResearchState) -> str:
    if state.error:
        return "error"
    return "summarize"


def after_summarize(state: ResearchState) -> str:
    if state.error:
        return "error"
    return "report"


def after_report(state: ResearchState) -> str:
    return "end"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def _build_research_graph() -> Any:
    """Build compiled research workflow graph."""
    graph = StateGraph(ResearchState)
    
    graph.add_node("search", search_node)
    graph.add_node("extract", extract_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("report", report_node)
    graph.add_node("error", error_node)
    
    graph.add_edge(START, "search")
    graph.add_conditional_edges(
        "search",
        after_search,
        {"extract": "extract", "error": "error"},
    )
    graph.add_conditional_edges(
        "extract",
        after_extract,
        {"summarize": "summarize", "error": "error"},
    )
    graph.add_conditional_edges(
        "summarize",
        after_summarize,
        {"report": "report", "error": "error"},
    )
    graph.add_conditional_edges(
        "report",
        after_report,
        {"end": END},
    )
    graph.add_edge("error", END)
    
    return graph.compile(checkpointer=InMemorySaver())


async def error_node(state: ResearchState) -> ResearchState:
    """Terminal error node."""
    state.terminal = True
    state.final_result = {
        "status": "failed",
        "error": state.error or "unknown error",
        "step": state.current_step,
    }
    return state


# ---------------------------------------------------------------------------
# Research Orchestrator
# ---------------------------------------------------------------------------

class ResearchOrchestrator:
    """Orchestrates research workflow."""
    
    def __init__(self) -> None:
        self._graph = _build_research_graph()
    
    async def execute(
        self,
        task_id: UUID,
        query: str,
        domain: str = "web",
    ) -> dict[str, Any]:
        """Execute research workflow.
        
        Args:
            task_id: Unique task identifier.
            query: Research query string.
            domain: Research domain ("web", "arxiv", or "general").
        
        Returns:
            Final result dict.
        """
        initial_state = ResearchState(
            task_id=task_id,
            query=query,
            domain=domain,
        )
        
        config = {"configurable": {"thread_id": str(task_id)}}
        result = await self._graph.ainvoke(initial_state, config)
        final = result.get("final_result")
        
        if final is not None:
            return final
        return {"status": "failed", "error": "no result"}


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

async def main() -> None:
    """CLI entry point for research."""
    import asyncio
    
    orch = ResearchOrchestrator()
    
    # Example: web search
    task_id = UUID("00000000-0000-0000-0000-000000000001")
    result = await orch.execute(
        task_id=task_id,
        query="What is LangGraph?",
        domain="web",
    )
    
    print(f"Status: {result.get('status')}")
    print(f"Summary:\n{result.get('summary', '')[:500]}")
    print(f"\nReport:\n{result.get('report', '')[:1000]}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
