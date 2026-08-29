"""LangGraph StateGraph orchestrator (Phase A).

Wraps the existing state-machine flow in a LangGraph StateGraph with checkpointing.
The graph path is selectable via settings.langgraph_enabled; the classic Orchestrator
remains the default.

Public API: GraphOrchestrator.execute() matches Orchestrator.execute() exactly.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import RunnableConfig
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from packages.config.settings import Settings, get_settings
from packages.contracts.enums import AgentResponseStatus, TaskStatus
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    ErrorDetail,
    TaskContext,
    TaskRequest,
)
from packages.core.errors import (
    AgentTimeoutError,
    AgentUnavailableError,
    AuthorizationError,
    BusinessOpsError,
    ErrorCode,
    HandoffCycleDetectedError,
    HandoffDepthExceededError,
    TaskTimeoutError,
    ToolExecutionError,
)
from packages.core.persistence import NoopTaskRecorder, TaskRecorder
from packages.core.policy import AllowAllPolicy, PolicyChecker
from packages.core.registry import InMemoryAgentRegistry
from packages.llm.base import LLMProvider

# ---------------------------------------------------------------------------
# Graph state (internal — not part of the public contract)
# ---------------------------------------------------------------------------


class GraphState(TypedDict):
    request: TaskRequest
    response: AgentResponse | None
    capability: str
    descriptor: AgentDescriptor | None
    current_status: TaskStatus
    attempt: int
    last_error: str | None
    handoff_target: str
    terminal: bool
    final_response: AgentResponse | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_transient(error_str: str) -> bool:
    """Mirrors Orchestrator._is_transient_error: only retry on timeout/tool errors.

    Takes error class name as string (serializable).
    """
    return any(t in error_str for t in ("TimeoutError", "ToolExecutionError"))


async def _get_recorder(config: RunnableConfig) -> TaskRecorder:
    return config.get("configurable", {}).get("recorder") or NoopTaskRecorder()


async def _get_policy(config: RunnableConfig) -> PolicyChecker:
    return config.get("configurable", {}).get("policy") or AllowAllPolicy()


async def _record(
    state: GraphState,
    config: RunnableConfig,
    status: TaskStatus,
) -> None:
    recorder = await _get_recorder(config)
    await recorder.record_transition(state["request"].task_id, status)


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


async def classify_node(
    state: GraphState,
    config: RunnableConfig,
) -> GraphState:
    """Classify the task capability from domain + action.

    Duplicates the Orchestrator.classify() body (10 lines). For Phase A this is
    acceptable; if both paths need a shared classifier later extract to a module.
    """
    from packages.core.input_filter import filter_input

    request = state["request"]
    text = request.payload.get("text") or request.payload.get("message")
    if isinstance(text, str) and text:
        filtered = filter_input(text)
        request.payload["text"] = filtered.clean_text
        if filtered.blocked:
            raise BusinessOpsError(
                f"Input rejected by filter: {filtered.block_reason}",
                task_id=request.task_id,
            )

    capability = f"{request.domain.value}.{request.action}"

    state["capability"] = capability
    state["current_status"] = TaskStatus.CLASSIFYING
    await _record(state, config, TaskStatus.CLASSIFYING)
    return state


async def route_node(
    state: GraphState,
    config: RunnableConfig,
) -> GraphState:
    """Route to the agent matching the capability, then run policy check."""
    capability = state["capability"]

    # Resolve from the registry captured during graph compilation
    global _graph_registry
    descriptor, handler = _graph_registry.get_by_capability(capability)

    state["descriptor"] = descriptor
    state["current_status"] = TaskStatus.ROUTING
    await _record(state, config, TaskStatus.ROUTING)

    # Policy check
    policy = await _get_policy(config)
    decision = await policy.check(
        capability=capability, context=state["request"].context
    )
    if not decision.allowed:
        raise AuthorizationError(
            decision.reason or "Capability not authorized", task_id=state["request"].task_id
        )
    # Store handler in state for the run_agent node
    state["handler"] = handler
    return state


async def run_agent_node(
    state: GraphState,
    config: RunnableConfig,
) -> GraphState:
    """Execute the routed agent with per-hop timeout, error handling, handoff detection.

    Retries up to 2 attempts on transient errors (timeout / tool errors) before
    surfacing the last error for dead-letter routing.
    """
    settings = get_settings()
    hop_timeout_s = settings.agent_hop_timeout_seconds
    capability = state["capability"]
    global _graph_registry
    descriptor, handler = _graph_registry.get_by_capability(capability)
    request = state["request"]

    state["current_status"] = TaskStatus.RUNNING
    await _record(state, config, TaskStatus.RUNNING)

    last_error_str: str | None = None
    for attempt in range(1, 3):  # max 2 attempts
        state["attempt"] = attempt
        try:
            response = await asyncio.wait_for(handler.handle(request), timeout=hop_timeout_s)
        except (TaskTimeoutError, AgentTimeoutError, ToolExecutionError) as exc:
            last_error_str = str(exc)
            if attempt == 2:
                # second attempt exhausted → route to dead_letter
                state["last_error"] = last_error_str
                return state
            continue
        except BusinessOpsError:
            raise
        except Exception as exc:
            raise AgentUnavailableError(
                f"Agent {descriptor.qualified_name} crashed: {exc}",
                task_id=request.task_id,
            ) from exc

        state["response"] = response

        # Check for handoff metadata
        handoff_metadata = response.metadata.get("handoff")
        if handoff_metadata and isinstance(handoff_metadata, dict):
            target = handoff_metadata.get("target_capability")
            if target:
                state["handoff_target"] = target

        return state

    # Exhausted retries without returning — should not reach here
    state["last_error"] = last_error_str or "Unknown transient error"
    return state


async def validate_node(
    state: GraphState,
    config: RunnableConfig,
) -> GraphState:
    """Validate the agent response. On validation error, route to dead-letter."""
    response = state["response"]
    if response is None:
        raise BusinessOpsError("No response to validate", task_id=state["request"].task_id)

    try:
        if response.status == AgentResponseStatus.SUCCESS:
            if not response.result:
                raise BusinessOpsError("Agent returned empty result")
        if response.metadata.get("requires_citations") and not response.citations:
            raise BusinessOpsError("Knowledge responses must include citations")
    except BusinessOpsError as exc:
        state["last_error"] = str(exc)
        state["current_status"] = TaskStatus.VALIDATING
        await _record(state, config, TaskStatus.VALIDATING)
        return state

    state["current_status"] = TaskStatus.VALIDATING
    await _record(state, config, TaskStatus.VALIDATING)
    return state


async def handoff_node(
    state: GraphState,
    config: RunnableConfig,
) -> GraphState:
    """Full handoff chain logic mirroring Orchestrator.handoff().

    Single composite node: depth check, cycle detection, build new request,
    route to target, policy check, execute, validate, merge.
    """
    request = state["request"]
    response = state["response"]
    target_capability = state["handoff_target"]
    registry = _graph_registry

    ctx = request.context
    current_depth = ctx.handoff_depth
    max_depth = ctx.max_handoff_depth

    # Record ROUTING before checks
    await _record(state, config, TaskStatus.ROUTING)

    # Depth check (guard against None)
    if max_depth is not None and current_depth >= max_depth:
        await _record(state, config, TaskStatus.FAILED)
        raise HandoffDepthExceededError(
            f"Handoff depth {current_depth} exceeds maximum {max_depth}",
            task_id=request.task_id,
            details={"current_depth": current_depth, "max_depth": max_depth},
        )

    # Cycle detection
    target_descriptor, _ = registry.get_by_capability(target_capability)
    target_agent = target_descriptor.qualified_name
    if target_agent in ctx.handoff_chain:
        await _record(state, config, TaskStatus.FAILED)
        raise HandoffCycleDetectedError(
            f"Handoff cycle detected: {' -> '.join(ctx.handoff_chain)} -> {target_agent}",
            task_id=request.task_id,
            details={"chain": ctx.handoff_chain, "attempted": target_agent},
        )

    # Build new handoff chain
    new_chain = ctx.handoff_chain + [response.agent]
    current_agent = response.agent

    handoff_request = TaskRequest(
        task_id=request.task_id,
        domain=request.domain,
        action=target_capability.split(".")[-1],
        payload=request.payload,
        context=TaskContext(
            user_id=ctx.user_id,
            organization_id=ctx.organization_id,
            channel=ctx.channel,
            locale=ctx.locale,
            trace_id=ctx.trace_id,
            handoff_chain=new_chain,
            handoff_depth=current_depth + 1,
            max_handoff_depth=max_depth,
            prior_results={**ctx.prior_results, current_agent: response.result},
        ),
        metadata=request.metadata,
    )

    # Route to target capability
    descriptor, handler = registry.get_by_capability(target_capability)

    # Policy check
    policy = await _get_policy(config)
    if policy:
        decision = await policy.check(
            capability=target_capability, context=handoff_request.context
        )
        if not decision.allowed:
            raise AuthorizationError(
                decision.reason or "Capability not authorized",
                task_id=handoff_request.task_id,
            )

    # Record RUNNING for the handoff agent
    await _record(state, config, TaskStatus.RUNNING)

    # Execute handoff target
    settings = get_settings()
    hop_timeout_s = settings.agent_hop_timeout_seconds

    try:
        handoff_response = await asyncio.wait_for(
            handler.handle(handoff_request), timeout=hop_timeout_s
        )
    except TimeoutError as exc:
        raise AgentTimeoutError(
            f"Agent {descriptor.qualified_name} timed out after {hop_timeout_s}s (hop budget)",
            task_id=request.task_id,
        ) from exc
    except BusinessOpsError:
        raise
    except Exception as exc:
        raise AgentUnavailableError(
            f"Agent {descriptor.qualified_name} crashed: {exc}",
            task_id=request.task_id,
        ) from exc

    # Validate handoff response
    if handoff_response.status == AgentResponseStatus.SUCCESS:
        if not handoff_response.result:
            raise BusinessOpsError("Handoff agent returned empty result")
    if handoff_response.metadata.get("requires_citations") and not handoff_response.citations:
        raise BusinessOpsError("Knowledge responses must include citations")

    # Record COMPLETED
    await _record(state, config, TaskStatus.COMPLETED)

    # Merge handoff response
    merged_result = {**response.result}
    if "knowledge" not in merged_result:
        merged_result["knowledge"] = {}
    merged_result["knowledge"].update(handoff_response.result)

    merged_citations = list(response.citations) + list(handoff_response.citations)

    merged_metadata = {**response.metadata}
    merged_metadata["handoff"] = {
        "from": response.agent,
        "to": descriptor.qualified_name,
        "merged_at": "orchestrator",
    }

    merged_response = AgentResponse(
        task_id=response.task_id,
        agent=response.agent,
        status=response.status,
        result=merged_result,
        citations=merged_citations,
        confidence=max(response.confidence, handoff_response.confidence),
        metadata=merged_metadata,
    )

    state["response"] = merged_response
    state["handoff_target"] = ""  # Clear so the graph doesn't re-enter handoff
    return state


async def dead_letter_node(
    state: GraphState,
    config: RunnableConfig,
) -> GraphState:
    """Record DEAD_LETTERED, build error response, mark terminal."""
    await _record(state, config, TaskStatus.DEAD_LETTERED)

    last_error = state["last_error"]
    error_code = ErrorCode.TASK_TIMEOUT.value
    error_message = "Task dead-lettered after retries"

    if last_error and "TimeoutError" in last_error:
        error_code = ErrorCode.TASK_TIMEOUT.value
        error_message = last_error
    elif last_error is not None:
        error_code = ErrorCode.INTERNAL_ERROR.value
        error_message = last_error

    error_response = AgentResponse(
        task_id=state["request"].task_id,
        agent="orchestrator",
        status=AgentResponseStatus.FAILED,
        error=ErrorDetail(code=error_code, message=error_message),
    )

    state["final_response"] = error_response
    state["terminal"] = True
    return state


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------


def after_run_agent(state: GraphState) -> str:
    if state["last_error"] is not None:
        if _is_transient(state["last_error"]) and state["attempt"] <= 2:
            return "run_agent"  # retry (max 2 attempts)
        return "dead_letter"
    if state["handoff_target"]:
        return "handoff"
    return "validate"


def after_validate(state: GraphState) -> str:
    if state["last_error"] is not None:
        return "dead_letter"
    return "end"


def after_handoff(state: GraphState) -> str:
    if state["last_error"] is not None:
        return "dead_letter"
    return "validate"


# ---------------------------------------------------------------------------
# Graph registry reference (set during compilation)
# ---------------------------------------------------------------------------

_graph_registry: InMemoryAgentRegistry | None = None


# ---------------------------------------------------------------------------
# Graph compilation
# ---------------------------------------------------------------------------


def _build_checkpointer(settings: Settings) -> InMemorySaver:
    """Build an InMemorySaver checkpointer.

    Uses InMemorySaver (available in this LangGraph 0.6.11 + langgraph-checkpoint 2.x env).
    For production with SQLite persistence, swap to SqliteSaver.from_conn_string().
    """
    return InMemorySaver()


def _build_graph(registry: InMemoryAgentRegistry) -> StateGraph:
    """Construct and compile the LangGraph StateGraph.

    Sets the module-level _graph_registry so node functions can access it.
    """
    global _graph_registry
    _graph_registry = registry

    graph = StateGraph(GraphState)

    graph.add_node("classify", classify_node)
    graph.add_node("route", route_node)
    graph.add_node("run_agent", run_agent_node)
    graph.add_node("validate", validate_node)
    graph.add_node("handoff", handoff_node)
    graph.add_node("dead_letter", dead_letter_node)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "route")
    graph.add_edge("route", "run_agent")
    graph.add_conditional_edges(
        "run_agent",
        after_run_agent,
        {
            "run_agent": "run_agent",
            "handoff": "handoff",
            "validate": "validate",
            "dead_letter": "dead_letter",
        },
    )
    graph.add_edge("handoff", "validate")
    graph.add_conditional_edges(
        "validate",
        after_validate,
        {
            "validate": "validate",  # placeholder for symmetry
            "end": END,
            "dead_letter": "dead_letter",
        },
    )
    graph.add_edge("dead_letter", END)

    return graph


# ---------------------------------------------------------------------------
# GraphOrchestrator
# ---------------------------------------------------------------------------


class GraphOrchestrator:
    """LangGraph-backed orchestrator. Public API matches Orchestrator.execute()."""

    def __init__(
        self,
        registry: InMemoryAgentRegistry,
        llm: LLMProvider,
        *,
        default_timeout_ms: int = 30_000,
    ) -> None:
        self._registry = registry
        self._llm = llm
        self._default_timeout_ms = default_timeout_ms
        self._settings = get_settings()
        self._graph = _build_graph(registry).compile(
            checkpointer=_build_checkpointer(self._settings)
        )

    async def execute(
        self,
        request: TaskRequest,
        *,
        recorder: TaskRecorder = NoopTaskRecorder(),
        policy: PolicyChecker = AllowAllPolicy(),
    ) -> AgentResponse:
        """Execute a task via the LangGraph StateGraph.

        Signature matches Orchestrator.execute() exactly.
        thread_id = str(request.task_id) for checkpoint scoping.
        recorder and policy passed via config['configurable'] for node access.
        """
        config: RunnableConfig = {
            "configurable": {
                "thread_id": str(request.task_id),
                "recorder": recorder,
                "policy": policy,
            },
            "run_name": "graph_orchestrator",
        }
        initial_state: GraphState = {
            "request": request,
            "response": None,
            "capability": "",
            "descriptor": None,
            "current_status": TaskStatus.PENDING,
            "attempt": 1,
            "last_error": None,
            "handoff_target": "",
            "terminal": False,
            "final_response": None,
        }
        result = await self._graph.ainvoke(initial_state, config)
        return result.get("final_response") or result.get("response")


__all__ = [
    "GraphOrchestrator",
    "GraphState",
]
