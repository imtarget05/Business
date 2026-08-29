# -*- coding: utf-8 -*-
"""Task source for the Business Ops Hub.

The plan (Task 2) aggregates Gmail unread + Calendar upcoming + *tasks* into a
single digest with concrete "things to do" alerts. There is no standalone task
agent in the platform yet, so an operational task is modeled as a lightweight,
local record:

    Task = (id, title, due, priority, done)

A :class:`TaskProvider` exposes them to the OpsHub agent. Two implementations
ship here:

* :class:`InMemoryTaskProvider` — default; seeded from ``config.yaml``
  (``ops.tasks``) so the digest is real data, never fabricated.
* :class:`StaticTaskProvider` — purely for dependency injection / tests.

The provider is injected into :class:`~agents.ops_hub.agent.OpsHubAgent`, so
the unit tests can pass hand-built lists without touching the network or disk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """A single operational to-do item (not an agent TaskRequest)."""

    id: str
    title: str
    due: datetime | None = None
    priority: str = "normal"  # low | normal | high
    done: bool = False
    source: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "due": self.due.isoformat() if self.due else None,
            "priority": self.priority,
            "done": self.done,
            "source": self.source,
        }


class TaskProvider:
    """Protocol-ish base for operational task sources."""

    async def list_tasks(self, *, include_done: bool = False) -> list[Task]:
        raise NotImplementedError


class StaticTaskProvider(TaskProvider):
    """Returns a fixed list — used by tests and manual injection."""

    def __init__(self, tasks: list[Task] | None = None) -> None:
        self._tasks = list(tasks or [])

    async def list_tasks(self, *, include_done: bool = False) -> list[Task]:
        if include_done:
            return list(self._tasks)
        return [t for t in self._tasks if not t.done]


class InMemoryTaskProvider(TaskProvider):
    """Default local task store, seeded from ``config.yaml`` (``ops.tasks``).

    Reads the optional ``ops.tasks`` list once at construction. Each entry may
    be a string (title) or a dict with keys ``title``, ``due`` (ISO 8601),
    ``priority``, ``done``. Unknown/malformed entries are skipped with a log
    warning rather than fabricating data.
    """

    def __init__(self, tasks: list[dict[str, Any]] | None = None) -> None:
        self._tasks: list[Task] = []
        for i, raw in enumerate(tasks or []):
            task = self._coerce(raw, i)
            if task is not None:
                self._tasks.append(task)

    @staticmethod
    def _coerce(raw: Any, index: int) -> Task | None:
        if isinstance(raw, str):
            return Task(id=f"local-{index}", title=raw)
        if not isinstance(raw, dict):
            logger.warning("ops.tasks[%s] bỏ qua (không phải dict/str)", index)
            return None
        title = raw.get("title")
        if not title:
            logger.warning("ops.tasks[%s] bỏ qua (thiếu title)", index)
            return None
        due: datetime | None = None
        due_raw = raw.get("due")
        if due_raw:
            try:
                due = datetime.fromisoformat(str(due_raw))
            except ValueError:
                logger.warning("ops.tasks[%s] due không hợp lệ: %r", index, due_raw)
        return Task(
            id=str(raw.get("id") or f"local-{index}"),
            title=str(title),
            due=due,
            priority=str(raw.get("priority", "normal")),
            done=bool(raw.get("done", False)),
            source="local",
        )

    async def list_tasks(self, *, include_done: bool = False) -> list[Task]:
        if include_done:
            return list(self._tasks)
        return [t for t in self._tasks if not t.done]


def build_task_provider(settings: Any | None = None) -> TaskProvider:
    """Build the default task provider.

    Tasks come from two real sources (never fabricated):

    * ``settings.ops_tasks`` — injected via env ``OPS_TASKS`` (JSON list).
    * ``ops.tasks`` in ``config.yaml`` — the optional ops section seeded by the
      operator. Read directly so the example tasks in config.yaml are honored
      even though pydantic-settings only loads environment variables.
    """
    tasks: list[dict[str, Any]] = []
    if settings is not None:
        tasks = list(getattr(settings, "ops_tasks", None) or [])
    tasks = tasks or _load_ops_tasks_from_config()
    return InMemoryTaskProvider(tasks=tasks)


def _load_ops_tasks_from_config() -> list[dict[str, Any]]:
    try:
        import os

        import yaml

        path = os.path.join(os.getcwd(), "config.yaml")
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return list((data.get("ops") or {}).get("tasks") or [])
    except Exception:  # noqa: BLE001
        return []
