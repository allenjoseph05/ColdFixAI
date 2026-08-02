"""A query-counting in-memory store, so planted defects are measurable.

Deliberately not Django, and not a real database. This fixture exists to
unit-test *instruments* against known ground truth: a query counter is correct
if it reports 21 when exactly 21 queries were issued, and establishing that
needs a subject whose true count is known by construction rather than measured.
Realism is the pinned target repository's job (ADR 011); this is the calibration
weight, not the specimen.

Consequences of that choice, stated so nobody re-litigates them later:

- Tests run in milliseconds with no service to start, so the fast subset stays
  fast and the fixtures cannot fail for environmental reasons.
- Every count is exact and deterministic. S-0.4 measured wall-clock timings
  drifting 12% between runs while guard counters reproduced to the byte; this
  store has only the second kind of number.
- It cannot catch anything that depends on real SQL, a real planner, or real
  connection behaviour. Those belong in integration tests against the target.

The store records **guard counters**, not just a query count, because the
project requires them on every metric: a change that halves the query count
while doubling rows returned is not an improvement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Row = dict[str, Any]


@dataclass
class Query:
    """One recorded access, with enough detail to compute guard counters."""

    table: str
    predicate: str
    rows_returned: int
    columns_returned: int

    @property
    def cells_returned(self) -> int:
        """A payload-size proxy, standing in for response bytes."""
        return self.rows_returned * self.columns_returned


@dataclass
class Store:
    """An in-memory table set that records every access.

    `select` is the only way in, so the query count cannot be understated by a
    caller reaching around the instrument.
    """

    tables: dict[str, list[Row]] = field(default_factory=dict)
    log: list[Query] = field(default_factory=list)

    def add(self, table: str, rows: list[Row]) -> None:
        self.tables.setdefault(table, []).extend(rows)

    def select(
        self,
        table: str,
        where: tuple[str, Any] | None = None,
        columns: tuple[str, ...] | None = None,
    ) -> list[Row]:
        """Read rows. Every call is one query, recorded.

        `where` is a single (column, value) equality, which is all the planted
        defects need. `columns` restricts the projection — the difference
        between selecting what you use and selecting everything is what makes
        over-fetch measurable.
        """
        rows = self.tables.get(table, [])
        if where is not None:
            column, value = where
            rows = [row for row in rows if row.get(column) == value]

        if columns is not None:
            rows = [{key: row[key] for key in columns if key in row} for row in rows]

        width = len(rows[0]) if rows else 0
        self.log.append(
            Query(
                table=table,
                predicate="all" if where is None else f"{where[0]}={where[1]!r}",
                rows_returned=len(rows),
                columns_returned=width,
            )
        )
        return rows

    def reset(self) -> None:
        """Clear the log, leaving the data. The between-measurements reset."""
        self.log.clear()

    @property
    def query_count(self) -> int:
        return len(self.log)

    @property
    def rows_returned(self) -> int:
        """Guard counter: total rows handed back across all queries."""
        return sum(query.rows_returned for query in self.log)

    @property
    def cells_returned(self) -> int:
        """Guard counter: payload-size proxy across all queries."""
        return sum(query.cells_returned for query in self.log)

    def counts_by_table(self) -> dict[str, int]:
        """Queries grouped by table.

        S-0.4 found that ablating one N+1 exposed a second one underneath it,
        and that grouping the residual by table was what made the second one
        nameable. The same grouping is available here.
        """
        grouped: dict[str, int] = {}
        for query in self.log:
            grouped[query.table] = grouped.get(query.table, 0) + 1
        return grouped


def build_store(authors: int, books_per_author: int) -> Store:
    """A two-table dataset with a fixed, uniform shape.

    Uniform on purpose. S-0.4 used uniform fixtures so that per-request work did
    not depend on which rows a page happened to contain; skew is S-3.3's
    subject, and a skewed fixture here would make every expected count a range
    instead of a number.
    """
    store = Store()
    store.add(
        "author",
        [
            {"id": i, "name": f"author-{i:04d}", "biography": "x" * 200, "born": 1900 + i}
            for i in range(authors)
        ],
    )
    store.add(
        "book",
        [
            {
                "id": author_id * books_per_author + n,
                "author_id": author_id,
                "title": f"book-{author_id:04d}-{n}",
                "synopsis": "y" * 500,
                "pages": 100 + n,
            }
            for author_id in range(authors)
            for n in range(books_per_author)
        ],
    )
    return store
