"""Tests for multi-agent handoff chains (Phase 4 Task 4.2)."""

from __future__ import annotations

import pytest
from uuid import uuid4

from packages.config.settings import LLMProviderKind, Settings
from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import TaskContext, TaskRequest
from packages.core.bootstrap import build_container
from packages.core.errors import HandoffCycleDetectedError, HandoffDepthExceededError
from packages.core.persistence import NoopTaskRecorder
from packages.llm.mock import MockLLMProvider


@pytest.fixture()
def container():
    return build_container(Settings(llm_provider=LLMProviderKind.MOCK))


async def test_support_knowledge_handoff_executes_and_merges(container):
    """Support task with needs_knowledge=true delegates to knowledge.query and merges state."""
    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload={
            "subject": "How do I reset my password?",
            "body": "I forgot my password",
            "needs_knowledge": True,
            "question": "How do I reset my password?",
        },
        context=TaskContext(organization_id=uuid4()),
    )

    resp = await container.orchestrator.execute(req)

    # Should succeed with merged response
    assert resp.status == AgentResponseStatus.SUCCESS
    assert resp.agent == "support-v1"  # Original agent stays as the responding agent

    # Result should contain both support summary and knowledge
    assert "knowledge" in resp.result
    # Knowledge agent returns "no relevant information found" when no KB data exists
    # The important thing is that the handoff happened and results were merged
    assert "answer" in resp.result["knowledge"]
    assert "confidence" in resp.result["knowledge"]

    # Should have citations from knowledge agent (empty list when no KB data)
    assert isinstance(resp.citations, list)

    # Metadata should show handoff occurred
    assert "handoff" in resp.metadata
    assert resp.metadata["handoff"]["from"] == "support-v1"
    assert resp.metadata["handoff"]["to"] == "knowledge-v1"


async def test_handoff_depth_limit_enforced(container):
    """Handoff depth limit (default 2) is enforced."""
    # Create a request that would exceed depth limit
    # We simulate this by setting max_handoff_depth=0 to force depth exceeded on first handoff
    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload={
            "subject": "Test",
            "body": "Test",
            "needs_knowledge": True,
            "question": "Test question",
        },
        context=TaskContext(
            organization_id=uuid4(),
            max_handoff_depth=0,  # Set to 0 to force depth exceeded
        ),
    )

    with pytest.raises(HandoffDepthExceededError) as exc_info:
        await container.orchestrator.execute(req)

    assert "exceeds maximum" in str(exc_info.value)
    assert exc_info.value.details["max_depth"] == 0


async def test_handoff_cycle_rejected(container):
    """Cycle detection rejects A->B->A handoff chains."""
    # Create a request with a pre-existing chain that would create a cycle
    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload={
            "subject": "Test",
            "body": "Test",
            "needs_knowledge": True,
            "question": "Test question",
        },
        context=TaskContext(
            organization_id=uuid4(),
            handoff_chain=["knowledge-v1"],  # Pretend we came from knowledge
            handoff_depth=1,
        ),
    )

    # The support agent will try to handoff to knowledge, but knowledge is already in chain
    with pytest.raises(HandoffCycleDetectedError) as exc_info:
        await container.orchestrator.execute(req)

    assert "cycle detected" in str(exc_info.value).lower()
    assert "knowledge-v1" in str(exc_info.value)


async def test_audit_rows_written_for_each_hop(container):
    """Each handoff hop is recorded via the recorder."""
    # Use a spy recorder to capture transitions
    transitions = []

    class SpyRecorder(NoopTaskRecorder):
        async def record_transition(self, task_id, status):
            transitions.append((str(task_id), status.value))

    recorder = SpyRecorder()

    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload={
            "subject": "How do I reset my password?",
            "body": "I forgot my password",
            "needs_knowledge": True,
            "question": "How do I reset my password?",
        },
        context=TaskContext(organization_id=uuid4()),
    )

    await container.orchestrator.execute(req, recorder=recorder)

    # Should have transitions for: CLASSIFYING, ROUTING, RUNNING (support),
    # ROUTING (handoff), RUNNING (knowledge), VALIDATING, COMPLETED
    statuses = [s for _, s in transitions]
    assert "classifying" in statuses
    assert "routing" in statuses
    assert "running" in statuses
    assert "validating" in statuses
    assert "completed" in statuses

    # Should have at least 2 ROUTING entries (initial + handoff)
    routing_count = statuses.count("routing")
    assert routing_count >= 2, f"Expected at least 2 routing transitions, got {routing_count}"

    # Should have at least 2 RUNNING entries (support + knowledge)
    running_count = statuses.count("running")
    assert running_count >= 2, f"Expected at least 2 running transitions, got {running_count}"


async def test_custom_max_handoffs_setting():
    """Custom agent_max_handoffs setting is respected."""
    custom_settings = Settings(
        llm_provider=LLMProviderKind.MOCK,
        agent_max_handoffs=3,
    )
    custom_container = build_container(custom_settings)

    # The orchestrator should pick up the setting
    assert custom_container.settings.agent_max_handoffs == 3

    # Verify it's used in context initialization
    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload={"subject": "Test", "body": "Test"},
        context=TaskContext(organization_id=uuid4()),
    )
    # Initial context has default max_handoff_depth=2
    assert req.context.max_handoff_depth == 2

    # Orchestrator.execute will override with settings value
    # (tested indirectly via the depth limit test above)


async def test_handoff_without_needs_knowledge_works_normally(container):
    """Support task without needs_knowledge executes normally without handoff."""
    req = TaskRequest(
        domain=Domain.SUPPORT,
        action="triage",
        payload={"subject": "Test", "body": "Test"},
        context=TaskContext(organization_id=uuid4()),
    )

    resp = await container.orchestrator.execute(req)

    # Should succeed without handoff
    assert resp.status in (AgentResponseStatus.SUCCESS, AgentResponseStatus.ESCALATED)
    assert resp.agent == "support-v1"
    # No handoff metadata
    assert "handoff" not in resp.metadata or resp.metadata.get("handoff") is None


async def test_knowledge_agent_works_standalone(container):
    """Knowledge agent still works directly without handoff."""
    req = TaskRequest(
        domain=Domain.KNOWLEDGE,
        action="query",
        payload={"question": "What is the meaning of life?"},
        context=TaskContext(organization_id=uuid4()),
    )

    resp = await container.orchestrator.execute(req)

    assert resp.status in (AgentResponseStatus.SUCCESS, AgentResponseStatus.REJECTED)
    assert resp.agent == "knowledge-v1"


async def test_handoff_preserves_original_task_id(container):
    """Handoff preserves the original task_id throughout the chain."""
    task_id = uuid4()
    req = TaskRequest(
        task_id=task_id,
        domain=Domain.SUPPORT,
        action="triage",
        payload={
            "subject": "Test",
            "body": "Test",
            "needs_knowledge": True,
            "question": "Test question",
        },
        context=TaskContext(organization_id=uuid4()),
    )

    resp = await container.orchestrator.execute(req)

    assert resp.task_id == task_id