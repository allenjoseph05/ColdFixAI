"""An ordinary task tracker. The control for S-2.8's real-time screening.

ADR 006: every defect carries a control, or the detector learns to say yes.
This file is that control, and it is the more important half of the fixture.

Everything here uses vocabulary a naive real-time detector would flag —
deadlines, priorities, schedulers, criticality, real-time updates — while being
exactly the kind of application this tool exists to speed up. The development
target pinned in ADR 011 is a helpdesk; if the screening refuses this, it
refuses its own target on the first day.

Plain dataclasses rather than Django models so the fixture type-checks without
the framework installed. The vocabulary is the point, not the ORM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Priority(StrEnum):
    """How urgent a task is. Not a scheduling priority."""

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
    tags: list[str] = field(default_factory=list)

    @property
    def overdue(self) -> bool:
        return self.deadline is not None and self.deadline < datetime.now()

    def is_hard_deadline(self) -> bool:
        """Whether missing this deadline matters to the customer.

        The phrase "hard deadline" is business vocabulary here and means a date
        in a contract. It is not a timing guarantee and nothing enforces it.
        """
        return self.priority is Priority.CRITICAL and self.deadline is not None
