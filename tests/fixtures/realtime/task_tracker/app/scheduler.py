"""Decides what order to show tasks in, and pushes real-time updates.

More control vocabulary for S-2.8. A class literally named `Scheduler`, a
priority queue, "real-time" in the sense every web application means it, and a
`deadline` sort key. None of it is a timing guarantee and none of it should be
refused.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class Priority(StrEnum):
    """Restated locally so the fixture is one self-contained file.

    These files exist to be *read* by the screening, not imported by it, and a
    cross-file import would make the fixture a package the type checker has to
    resolve. The vocabulary is the point, not the wiring.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Task:
    """A unit of work with a deadline somebody will probably miss."""

    title: str
    deadline: datetime | None = None
    priority: Priority = Priority.NORMAL
    is_mission_critical: bool = False

    @property
    def overdue(self) -> bool:
        return self.deadline is not None and self.deadline < datetime.now(tz=UTC)


_RANK = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.NORMAL: 2, Priority.LOW: 3}

# The far future, so a task with no deadline sorts last rather than crashing.
_NO_DEADLINE = datetime.max.replace(tzinfo=UTC)


class Scheduler:
    """Orders tasks for display. Soft, advisory, and entirely best-effort."""

    def __init__(self, realtime_updates: bool = True) -> None:
        self.realtime_updates = realtime_updates

    def by_urgency(self, tasks: Iterable[Task]) -> Iterator[Task]:
        """Most urgent first: priority, then deadline.

        A heap because the board redraws on every websocket message and sorting
        the whole list each time showed up in a profile once.
        """
        heap: list[tuple[int, datetime, int, Task]] = []
        for index, task in enumerate(tasks):
            deadline = task.deadline
            if deadline is not None and deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            heapq.heappush(heap, (_RANK[task.priority], deadline or _NO_DEADLINE, index, task))
        while heap:
            yield heapq.heappop(heap)[3]

    def missed_deadlines(self, tasks: Iterable[Task]) -> list[Task]:
        return [task for task in tasks if task.overdue]
