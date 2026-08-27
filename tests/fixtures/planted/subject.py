"""The planted store, wrapped as something a screen can drive.

Five screening test modules define this pair by hand — `Subject` and a snapshot
`StoreReset` — because each needed a `BoundWorkload` and there was nowhere
shared to put them. Epic 16's composition check needed a sixth, from a different
directory, and a cross-directory import between test modules is the module-name
collision `pyproject.toml` already documents for `conftest`: mypy resolves
`tests/screening/test_flagging.py` as both `test_flagging` and
`screening.test_flagging` and refuses.

So the canonical pair lives here, under `fixtures/`, which `pythonpath` makes
importable from any test directory under exactly one name. The five copies are
left alone: consolidating them is a tidy-up that touches five passing modules
for no behavioural gain, and doing it inside a composition check would mix a
refactor into a finding.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from coldfix.primitives.counters import DB_ROWS
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.screening.workload import RESPONSE_BYTES
from fixtures.planted.store import Store, build_store

CELLS = "cells_returned"
"""What the store returned, counted in cells rather than rows.

The guard counter for a query count: halving the queries while quadrupling the
cells returned is not an improvement, and only one of the two is visible without
this.
"""


@dataclass
class Subject:
    """One planted call, its store, and the identities it has run under."""

    call: Any
    store: Store = field(default_factory=Store)
    processes: list[str] = field(default_factory=list)

    def scale(self, n: int) -> None:
        self.store = build_store(authors=n, books_per_author=2)

    def invoke(self) -> object:
        return self.call(self.store)

    def process_identity(self) -> str:
        self.processes.append(f"container-{len(self.processes)}")
        return self.processes[-1]

    def payload(self) -> Mapping[str, float]:
        return {
            CELLS: float(self.store.cells_returned),
            DB_ROWS: float(self.store.rows_returned),
            RESPONSE_BYTES: float(self.store.cells_returned * 8),
        }


class StoreReset(ResetMechanism):
    """Snapshot and restore, so a sweep measures each scale point from a baseline."""

    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, subject: Subject) -> None:
        self.subject = subject
        self._snapshot: Store | None = None

    def prepare(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def begin(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def reset(self) -> None:
        if self._snapshot is None:
            raise ResetNotPreparedError(self.strategy)
        self.subject.store = deepcopy(self._snapshot)
