"""Task lifecycle state machine (STEP 0.5).

Transitions are explicit; arbitrary state changes raise `TaskStateError`.
"""

from __future__ import annotations

from packages.contracts.enums import TaskStatus
from packages.core.errors import TaskStateError

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {TaskStatus.CLASSIFYING, TaskStatus.CANCELLED, TaskStatus.DEAD_LETTERED}
    ),
    TaskStatus.CLASSIFYING: frozenset(
        {TaskStatus.ROUTING, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.DEAD_LETTERED}
    ),
    TaskStatus.ROUTING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.ESCALATED,
            TaskStatus.CANCELLED,
            TaskStatus.DEAD_LETTERED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.VALIDATING, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.DEAD_LETTERED}
    ),
    TaskStatus.VALIDATING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.ESCALATED, TaskStatus.DEAD_LETTERED}
    ),
    # Terminal states
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),  # retries are modelled as NEW agent_runs, not state flips
    TaskStatus.ESCALATED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.DEAD_LETTERED: frozenset(),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


class TaskStateMachine:
    """Guard object owning the current status of one task."""

    def __init__(self, initial: TaskStatus = TaskStatus.PENDING) -> None:
        self._status = initial

    @property
    def status(self) -> TaskStatus:
        return self._status

    def transition(self, target: TaskStatus) -> TaskStatus:
        if not can_transition(self._status, target):
            raise TaskStateError(
                f"Illegal task transition {self._status.value!r} -> {target.value!r}",
                details={"current": self._status.value, "target": target.value},
            )
        previous = self._status
        self._status = target
        return previous

    def is_terminal(self) -> bool:
        return not ALLOWED_TRANSITIONS[self._status]
