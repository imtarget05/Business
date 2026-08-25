"""Orchestrator skeleton (Phase 0).

Flow: PENDING -> CLASSIFYING -> ROUTING -> RUNNING -> VALIDATING -> terminal.
Routing is registry-driven by capability string — no if/else on domain.

Phase 0 scope: happy-path wiring with MockLLMProvider. Persistence, retries
and full policy checks land in Phase 1.
"""

from __future__ import annotations

import asyncio

from packages.contracts.enums import AgentResponseStatus, TaskStatus
from packages.contracts.models import (
    AgentDescriptor,
    AgentResponse,
    Citation,
    ErrorDetail,
    TaskRequest,
)
from packages.contracts.state_machine import TaskStateMachine
from packages.core.agent_base import DomainAgent
from packages.core.errors import (
    AgentTimeoutError,
    AgentUnavailableError,
    AuthorizationError,
    BusinessOpsError,
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

            timeout_s = (descriptor.timeout_ms or self._default_timeout_ms) / 1000
            sm.transition(TaskStatus.RUNNING)
            await self._record(recorder, request.task_id, TaskStatus.RUNNING)
            try:
                response = await asyncio.wait_for(handler.handle(request), timeout=timeout_s)
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

        except BusinessOpsError as exc:
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


__all__ = ["Orchestrator", "Citation", "RoutingError"]
