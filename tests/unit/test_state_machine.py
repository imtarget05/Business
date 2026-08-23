"""Task lifecycle state machine tests (STEP 0.5)."""

from __future__ import annotations

import pytest

from packages.contracts.enums import TaskStatus
from packages.contracts.state_machine import (
    ALLOWED_TRANSITIONS,
    TaskStateMachine,
    can_transition,
)
from packages.core.errors import TaskStateError


def test_happy_path_transitions() -> None:
    sm = TaskStateMachine()
    for target in (
        TaskStatus.CLASSIFYING,
        TaskStatus.ROUTING,
        TaskStatus.RUNNING,
        TaskStatus.VALIDATING,
        TaskStatus.COMPLETED,
    ):
        sm.transition(target)
    assert sm.is_terminal()


def test_failure_from_running() -> None:
    sm = TaskStateMachine(TaskStatus.RUNNING)
    sm.transition(TaskStatus.FAILED)
    assert sm.is_terminal()


def test_arbitrary_transition_rejected() -> None:
    sm = TaskStateMachine()
    with pytest.raises(TaskStateError):
        sm.transition(TaskStatus.COMPLETED)  # PENDING -> COMPLETED is illegal


def test_completed_is_absorbing() -> None:
    sm = TaskStateMachine(TaskStatus.COMPLETED)
    with pytest.raises(TaskStateError):
        sm.transition(TaskStatus.RUNNING)


def test_all_enum_states_have_transition_rules() -> None:
    assert set(ALLOWED_TRANSITIONS.keys()) == set(TaskStatus)


def test_can_transition_helper() -> None:
    assert can_transition(TaskStatus.PENDING, TaskStatus.CANCELLED)
    assert not can_transition(TaskStatus.PENDING, TaskStatus.VALIDATING)
