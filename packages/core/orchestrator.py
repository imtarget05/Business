"""Orchestrator skeleton (Phase 0).

Flow: PENDING -> CLASSIFYING -> ROUTING -> RUNNING -> VALIDATING -> terminal.
Routing is registry-driven by capability string — no if/else on domain.

Phase 0 scope: happy-path wiring with MockLLMProvider. Persistence, retries
and full policy checks land in Phase 1.

Phase 4 Task 4.2: Multi-agent handoff chains with depth limit and audit.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from packages.config.settings import get_settings
from packages.contracts.enums import AgentResponseStatus, TaskStatus
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    Citation,
    ErrorDetail,
    TaskContext,
    TaskRequest,
)
from packages.contracts.state_machine import TaskStateMachine
from packages.core.agent_base import DomainAgent
from packages.core.errors import (
    AgentTimeoutError,
    AgentUnavailableError,
    AuthorizationError,
    BusinessOpsError,
    HandoffCycleDetectedError,
    HandoffDepthExceededError,
    RoutingError,
)
from packages.core.persistence import NoopTaskRecorder, TaskRecorder
from packages.core.policy import AllowAllPolicy, PolicyChecker
from packages.core.registry import InMemoryAgentRegistry
from packages.llm.base import LLMProvider
from packages.observability.context import get_context
from packages.observability.logging import get_logger

logger = get_logger("orchestrator")


class Orchestrator:
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

    @staticmethod
    async def _record(
        recorder: TaskRecorder | None, task_id, status: TaskStatus
    ) -> None:
        """Persist a lifecycle transition when a recorder is provided."""
        if recorder is not None:
            await recorder.record_transition(task_id, status)

    async def classify(self, request: TaskRequest) -> str:
        """Return the capability the task should be routed to.

        Phase 0 derives the capability deterministically from domain+action
        while still exercising the LLM abstraction (the classifier becomes a
        real structured-LLM call in Phase 1). No if/else on specific domains.
        """
        prompt = (
            "Classify this task and return the target capability.\n"
            f"domain={request.domain.value} action={request.action}"
        )
        await self._llm.generate(
            prompt,
            system="You are the orchestrator classifier.",
            max_tokens=64,
        )
        return f"{request.domain.value}.{request.action}"

    async def route(self, capability: str) -> tuple[AgentDescriptor, DomainAgent]:
        descriptor, handler = self._registry.get_by_capability(capability)
        return descriptor, handler  # type: ignore[return-value]

    async def validate(self, response: AgentResponse) -> None:
        """Basic output validation. Rich evaluators arrive in Phase 5."""
        if response.status == AgentResponseStatus.SUCCESS:
            if not response.result:
                raise BusinessOpsError("Agent returned empty result")
        # knowledge-style agents should cite sources when they succeed
        if response.metadata.get("requires_citations") and not response.citations:
            raise BusinessOpsError("Knowledge responses must include citations")

    async def _execute_agent(
        self,
        request: TaskRequest,
        descriptor: AgentDescriptor,
        handler: DomainAgent,
        *,
        recorder: TaskRecorder = NoopTaskRecorder(),
    ) -> AgentResponse:
        """Execute a single agent, handling timeouts and errors."""
        timeout_s = (descriptor.timeout_ms or self._default_timeout_ms) / 1000
        try:
            response = await asyncio.wait_for(
                handler.handle(request), timeout=timeout_s
            )
        except TimeoutError as exc:  # py>=3.11 alias of asyncio.TimeoutError
            raise AgentTimeoutError(
                f"Agent {descriptor.qualified_name} timed out",
                task_id=request.task_id,
            ) from exc
        except BusinessOpsError:
            raise
        except Exception as exc:
            # Infrastructure failure inside an agent (DB unreachable,
            # missing table, provider crash) -> typed FAILED, never a 500.
            raise AgentUnavailableError(
                f"Agent {descriptor.qualified_name} crashed: {exc}",
                task_id=request.task_id,
            ) from exc
        return response

    async def handoff(
        self,
        request: TaskRequest,
        response: AgentResponse,
        target_capability: str,
        *,
        recorder: TaskRecorder = NoopTaskRecorder(),
        policy: PolicyChecker = AllowAllPolicy(),
    ) -> AgentResponse:
        """
        Delegate to another agent mid-flow, passing state through contracts.

        This implements the handoff chain: agent A -> agent B.
        - Depth limit enforced via TaskContext.max_handoff_depth (configurable)
        - Cycle detection via TaskContext.handoff_chain
        - Each hop recorded as an agent_run row via the recorder
        """
        ctx = request.context
        current_depth = ctx.handoff_depth
        max_depth = ctx.max_handoff_depth

        # Record the handoff transition BEFORE checks (audit trail for failed hops)
        await self._record(recorder, request.task_id, TaskStatus.ROUTING)

        # Check depth limit
        if current_depth >= max_depth:
            await self._record(recorder, request.task_id, TaskStatus.FAILED)
            raise HandoffDepthExceededError(
                f"Handoff depth {current_depth} exceeds maximum {max_depth}",
                task_id=request.task_id,
                details={"current_depth": current_depth, "max_depth": max_depth},
            )

        # Check for cycles
        # Look up the target agent's qualified name from the capability
        target_descriptor, _ = await self.route(target_capability)
        target_agent = target_descriptor.qualified_name
        if target_agent in ctx.handoff_chain:
            await self._record(recorder, request.task_id, TaskStatus.FAILED)
            raise HandoffCycleDetectedError(
                f"Handoff cycle detected: {' -> '.join(ctx.handoff_chain)} -> {target_agent}",
                task_id=request.task_id,
                details={"chain": ctx.handoff_chain, "attempted": target_agent},
            )

        # Build new handoff chain
        new_chain = ctx.handoff_chain + [response.agent]
        current_agent = response.agent

        # Create new request for the handoff target
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

        # Route to the target capability
        descriptor, handler = await self.route(target_capability)

        # Policy check
        decision = await policy.check(
            capability=target_capability, context=handoff_request.context
        )
        if not decision.allowed:
            raise AuthorizationError(
                decision.reason or "Capability not authorized",
                task_id=handoff_request.task_id,
            )

        # Record RUNNING state for the handoff agent
        await self._record(recorder, request.task_id, TaskStatus.RUNNING)

        # Execute the handoff target
        handoff_response = await self._execute_agent(
            handoff_request, descriptor, handler, recorder=recorder
        )

        # Validate the handoff response
        await self.validate(handoff_response)

        # Record COMPLETED for this handoff hop
        await self._record(recorder, request.task_id, TaskStatus.COMPLETED)

        logger.info(
            "handoff_completed",
            extra={
                "from_agent": current_agent,
                "to_agent": descriptor.qualified_name,
                "depth": current_depth + 1,
            },
        )

        return handoff_response

    def _merge_handoff_response(
        self, original: AgentResponse, handoff: AgentResponse
    ) -> AgentResponse:
        """Merge handoff response into original response.

        Combines results, citations, and metadata from both agents.
        The original agent's result is preserved and extended with
        the handoff agent's knowledge.
        """
        merged_result = {**original.result}
        if "knowledge" not in merged_result:
            merged_result["knowledge"] = {}
        merged_result["knowledge"].update(handoff.result)

        # Merge citations
        merged_citations = list(original.citations) + list(handoff.citations)

        # Merge metadata
        merged_metadata = {**original.metadata}
        merged_metadata["handoff"] = {
            "from": original.agent,
            "to": handoff.agent,
            "merged_at": "orchestrator",
        }

        return AgentResponse(
            task_id=original.task_id,
            agent=original.agent,
            status=original.status,
            result=merged_result,
            citations=merged_citations,
            confidence=max(original.confidence, handoff.confidence),
            metadata=merged_metadata,
        )

    async def execute(
        self,
        request: TaskRequest,
        *,
        recorder: TaskRecorder = NoopTaskRecorder(),
        policy: PolicyChecker = AllowAllPolicy(),
    ) -> AgentResponse:
        sm = TaskStateMachine()
        ctx = get_context()
        ctx.task_id = request.task_id
        try:
            sm.transition(TaskStatus.CLASSIFYING)
            await self._record(recorder, request.task_id, TaskStatus.CLASSIFYING)
            capability = await self.classify(request)

            sm.transition(TaskStatus.ROUTING)
            await self._record(recorder, request.task_id, TaskStatus.ROUTING)
            descriptor, handler = await self.route(capability)

            decision = await policy.check(
                capability=capability, context=request.context
            )
            if not decision.allowed:
                raise AuthorizationError(
                    decision.reason or "Capability not authorized",
                    task_id=request.task_id,
                )

            sm.transition(TaskStatus.RUNNING)
            await self._record(recorder, request.task_id, TaskStatus.RUNNING)

            # Initialize handoff state from settings if not explicitly set (None sentinel)
            if request.context.max_handoff_depth is None and hasattr(
                self._settings, "agent_max_handoffs"
            ):
                request.context.max_handoff_depth = self._settings.agent_max_handoffs

            response = await self._execute_agent(
                request, descriptor, handler, recorder=recorder
            )

            # Check if the agent requested a handoff
            handoff_metadata = response.metadata.get("handoff")
            if handoff_metadata and isinstance(handoff_metadata, dict):
                target_capability = handoff_metadata.get("target_capability")
                if target_capability:
                    # Perform the handoff
                    handoff_response = await self.handoff(
                        request, response, target_capability, recorder=recorder, policy=policy
                    )
                    # Merge handoff response into the original response
                    response = self._merge_handoff_response(response, handoff_response)

            sm.transition(TaskStatus.VALIDATING)
            await self._record(recorder, request.task_id, TaskStatus.VALIDATING)
            await self.validate(response)

            sm.transition(TaskStatus.COMPLETED)
            await self._record(recorder, request.task_id, TaskStatus.COMPLETED)
            logger.info(
                "task_completed",
                extra={"agent": descriptor.qualified_name, "status": response.status.value},
            )
            return response

        except (HandoffDepthExceededError, HandoffCycleDetectedError) as exc:
            # Handoff-specific errors: record terminal FAILED state, then re-raise
            # so API layer receives the typed error
            exc.task_id = exc.task_id or request.task_id
            if not sm.is_terminal():
                sm.transition(TaskStatus.FAILED)
                await self._record(recorder, request.task_id, TaskStatus.FAILED)
            logger.warning(
                "task_failed",
                extra={"error_code": exc.code.value, "state": sm.status.value},
            )
            raise

        except BusinessOpsError as exc:
            # Other business errors: record terminal state and return error response
            exc.task_id = exc.task_id or request.task_id
            if not sm.is_terminal():
                target = (
                    TaskStatus.ESCALATED
                    if sm.status == TaskStatus.VALIDATING
                    else TaskStatus.FAILED
                )
                sm.transition(target)
                await self._record(recorder, request.task_id, target)
            logger.warning(
                "task_failed",
                extra={"error_code": exc.code.value, "state": sm.status.value},
            )
            return AgentResponse(
                task_id=request.task_id,
                agent="orchestrator",
                status=(
                    AgentResponseStatus.TIMEOUT
                    if isinstance(exc, AgentTimeoutError)
                    else AgentResponseStatus.FAILED
                ),
                error=ErrorDetail(code=exc.code.value, message=exc.message),
            )


__all__ = [
    "Orchestrator",
    "Citation",
    "RoutingError",
    "HandoffDepthExceededError",
    "HandoffCycleDetectedError",
]