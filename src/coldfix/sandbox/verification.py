"""Prove a reset works here before trusting it, and say precisely how it failed.

Epic 2, S-2.7. S-2.6 implements three ways of returning the database to its
baseline, and none of them is trusted on the strength of being implemented. This
runs each one ten times against the actual project and reports what drifted.

**The check has four database parts because one part is not enough, and that is
measured rather than assumed.** S-0.5 ran plain rollback ten times and every row
count came back identical — so did every content hash, so did every `max(id)` —
while the sequences climbed 250 higher than they started. Row counting alone
certified a reset that was failing on every cycle. The four parts are row
counts, content hashes, maximum ids and sequence positions, and the fourth is
the one that catches the defect the first three miss.

**The workload's own observation is compared too**, and it must be *identical*
across cycles. That is the right expectation rather than a coincidence: after a
correct reset the sequences are back, so the same workload inserts the same rows
with the same ids and has the same thing to report. It catches state the
fingerprint cannot reach — a table it cannot hash, a file, anything the workload
can see and this module cannot.

**Cache state needs a different check, and the obvious one does not work.**
S-0.5 found a Django `QuerySet` still reporting a row that had been rolled back,
because the rows sit in a Python object no database-side reset can reach. The
tempting move is to catch it through the observation — but a cached workload
returns the same value every cycle, and so does a correct one, because a correct
reset makes every cycle identical. The two are indistinguishable by output.

So the harness checks the condition that makes a cache *possible* instead:
`process_identity` must **differ** on every cycle. A process that survives from
one cycle to the next is a process that can carry a cache no reset here will
ever clear. ADR 025 claims this is already guaranteed, because S-2.1 destroys
the container after every run — this is what turns that claim into something
checked rather than assumed, and what would notice if containers were ever made
persistent between runs.

Supplying no `process_identity` skips the check, and skipping it is exactly how
S-0.5's defect returns unnoticed.

**Failure is a report, not an exception.** A strategy that does not work here is
an ordinary finding — `SNAPSHOT_RESTORE` exists precisely because rollback
cannot undo what another connection committed, which is the normal case for a
containerised workload. The caller tries the next strategy, and only running out
of strategies is an error.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import psycopg

from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy, SequenceValue, capture_sequences

# S-0.5 ran ten. Enough that an accumulating drift of one unit per cycle is
# visible against a baseline, cheap enough to run before every investigation.
DEFAULT_CYCLES = 10

_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema")


class VerificationError(Exception):
    """Reset could not be verified."""


class NoReliableResetError(VerificationError):
    """Every candidate strategy drifted, so no experiment can be trusted here.

    This is the honest end of the road rather than a bug. The system's whole
    method is to measure, change one thing and measure again; without a reset
    that returns the starting state, the second measurement is of a different
    program and every conclusion drawn from the pair is wrong.

    Carries every strategy's report, because "nothing worked" is not actionable
    and "rollback drifted on sequences, snapshot drifted on nothing but could
    not run, container restart timed out" is.
    """

    def __init__(self, reports: Sequence[VerificationReport]) -> None:
        self.reports = tuple(reports)
        detail = "\n\n".join(report.diagnostic() for report in reports)
        super().__init__(
            "no reset strategy returned this project to its starting state, so no "
            f"experiment run against it would be comparable with another.\n\n{detail}"
        )


@dataclass(frozen=True)
class Fingerprint:
    """What the database looked like, in the four ways that turned out to matter.

    Content hashes exist because a row count cannot see an `UPDATE`. Maximum ids
    exist because a delete-and-reinsert leaves the count identical. Sequences
    exist because S-0.5 proved the other three miss the defect entirely.
    """

    row_counts: dict[str, int]
    content_hashes: dict[str, str]
    max_ids: dict[str, int | None]
    sequences: tuple[SequenceValue, ...]


@dataclass(frozen=True)
class Drift:
    """One way a cycle failed to come back, named specifically enough to fix."""

    cycle: int
    kind: str
    subject: str
    expected: str
    found: str

    def __str__(self) -> str:
        return (
            f"cycle {self.cycle}: {self.kind} of {self.subject} was {self.expected}, "
            f"expected {self.found}"
        )


@dataclass(frozen=True)
class VerificationReport:
    """Whether a strategy returned the starting state, and what moved if not."""

    strategy: ResetStrategy
    cycles: int
    drift: tuple[Drift, ...] = ()
    failure: str | None = None

    @property
    def reliable(self) -> bool:
        """No drift on any check, on any cycle, and nothing raised."""
        return not self.drift and self.failure is None

    def diagnostic(self) -> str:
        """A description a person can act on without reading the code.

        Drift is reported by kind and by the first cycle each subject moved in,
        not as every observation. Ten cycles of an accumulating sequence produce
        ten near-identical lines that say one thing.
        """
        if self.reliable:
            return f"{self.strategy.value}: reliable over {self.cycles} cycles"
        if self.failure is not None:
            return f"{self.strategy.value}: could not run — {self.failure}"

        first_by_subject: dict[tuple[str, str], Drift] = {}
        for item in self.drift:
            first_by_subject.setdefault((item.kind, item.subject), item)

        lines = [
            f"{self.strategy.value}: drifted on {len(first_by_subject)} subject(s) "
            f"over {self.cycles} cycles"
        ]
        lines += [f"  - {item}" for item in first_by_subject.values()]
        return "\n".join(lines)


@dataclass(frozen=True)
class VerifiedReset:
    """A strategy that has been shown to work on this project.

    Constructing one requires a passing report, the same way `VerifiedDatabase`
    requires a passing URL. S-2.6's acceptance criterion says each strategy is
    verified *before use*; making the verified state a type is what turns that
    from an instruction into something a caller cannot skip, because the thing
    they need to run experiments is the thing only verification produces.
    """

    mechanism: ResetMechanism
    report: VerificationReport = field(repr=False)

    def __post_init__(self) -> None:
        if not self.report.reliable:
            message = (
                f"{self.report.strategy.value} did not verify, so it cannot be used:\n"
                f"{self.report.diagnostic()}"
            )
            raise VerificationError(message)

    @property
    def strategy(self) -> ResetStrategy:
        return self.mechanism.strategy


def capture_fingerprint(database: VerifiedDatabase) -> Fingerprint:
    """The four-part state of every table and sequence in the user's schemas.

    A fresh connection each time, because `SNAPSHOT_RESTORE` drops and recreates
    the database underneath — a connection held across a reset would be pointing
    at something that no longer exists.
    """
    with psycopg.connect(database.dsn) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname <> ALL(%s) ORDER BY tablename",
                (list(_SYSTEM_SCHEMAS),),
            ).fetchall()
        ]

        row_counts: dict[str, int] = {}
        content_hashes: dict[str, str] = {}
        max_ids: dict[str, int | None] = {}

        for table in tables:
            row_counts[table] = _scalar_int(connection, f'SELECT count(*) FROM "{table}"')
            # Ordering by the row's own text is what makes this stable: physical
            # row order is not preserved by a restore and would otherwise make
            # every strategy look like it drifted.
            content_hashes[table] = str(
                _scalar(
                    connection,
                    f"SELECT coalesce(md5(string_agg(t::text, '' ORDER BY t::text)), '') "
                    f'FROM "{table}" t',
                )
            )
            max_ids[table] = _max_id(connection, table)

        sequences = capture_sequences(connection)

    return Fingerprint(
        row_counts=row_counts,
        content_hashes=content_hashes,
        max_ids=max_ids,
        sequences=sequences,
    )


def verify(
    mechanism: ResetMechanism,
    database: VerifiedDatabase,
    workload: Callable[[], object],
    *,
    cycles: int = DEFAULT_CYCLES,
    process_identity: Callable[[], object] | None = None,
) -> VerificationReport:
    """Run seed → workload → reset `cycles` times and report what did not come back.

    `workload` returns an observation — whatever the caller considers the
    meaningful result of running it. It must be **identical** on every cycle,
    which catches state the fingerprint cannot reach.

    `process_identity` is the cache check and works the other way round: it must
    **differ** on every cycle. A process that survives from one cycle to the next
    can carry a cached row that no database reset will clear, and comparing
    output cannot detect that — a cached workload and a correct one both report
    the same thing every time. Supplying nothing skips the check, and skipping
    it is how S-0.5's cached-queryset defect returns unnoticed.

    Never raises for an unreliable reset. A strategy that does not work here is
    a finding, and the caller's response is to try the next one.
    """
    if cycles < 1:
        message = f"cycles must be at least 1, got {cycles}"
        raise ValueError(message)

    try:
        mechanism.prepare()
        baseline = capture_fingerprint(database)
    except Exception as error:  # noqa: BLE001
        # A strategy that cannot even establish a baseline is unusable here, and
        # that is a report rather than a crash for the same reason drift is: the
        # caller has other strategies to try.
        return VerificationReport(mechanism.strategy, cycles, failure=repr(error))

    drift: list[Drift] = []
    first_observation: object = None
    seen_identities: dict[str, int] = {}

    for cycle in range(1, cycles + 1):
        try:
            with mechanism.cycle():
                observation = workload()
                identity = None if process_identity is None else repr(process_identity())
        except Exception as error:  # noqa: BLE001
            return VerificationReport(mechanism.strategy, cycles, tuple(drift), failure=repr(error))

        if cycle == 1:
            first_observation = observation
        elif repr(observation) != repr(first_observation):
            drift.append(
                Drift(
                    cycle=cycle,
                    kind="observation",
                    subject="the workload's own result",
                    expected=repr(observation),
                    found=repr(first_observation),
                )
            )

        if identity is not None:
            if identity in seen_identities:
                drift.append(
                    Drift(
                        cycle=cycle,
                        kind="process",
                        subject="the process the workload ran in",
                        expected=f"{identity}, the same one as cycle {seen_identities[identity]}",
                        found="a process that did not outlive the previous cycle",
                    )
                )
            seen_identities[identity] = cycle

        drift += _compare(cycle, baseline, capture_fingerprint(database))

    return VerificationReport(mechanism.strategy, cycles, tuple(drift))


def choose_reset(
    candidates: Iterable[ResetMechanism],
    database: VerifiedDatabase,
    workload: Callable[[], object],
    *,
    cycles: int = DEFAULT_CYCLES,
    process_identity: Callable[[], object] | None = None,
) -> VerifiedReset:
    """The first candidate that verifies, trying them in the order given.

    Order is the caller's, and should be cheapest first: S-0.5 measured 19 ms
    for a rollback against 163 ms for a snapshot and seconds for a container
    restart, and the cheap one is correct often enough to be worth trying.

    Raises:
        NoReliableResetError: every candidate drifted or failed. The error
            carries all their reports, because knowing that rollback drifted on
            sequences and snapshot could not run is actionable and "nothing
            worked" is not.
    """
    reports: list[VerificationReport] = []
    for mechanism in candidates:
        report = verify(
            mechanism, database, workload, cycles=cycles, process_identity=process_identity
        )
        reports.append(report)
        if report.reliable:
            return VerifiedReset(mechanism=mechanism, report=report)

    if not reports:
        message = "no candidate strategies were offered, so none could be verified"
        raise VerificationError(message)
    raise NoReliableResetError(reports)


def _compare(cycle: int, baseline: Fingerprint, current: Fingerprint) -> list[Drift]:
    """Every way `current` differs from `baseline`, reported rather than summarised."""
    drift: list[Drift] = []

    for kind, before, after in (
        ("row count", baseline.row_counts, current.row_counts),
        ("content hash", baseline.content_hashes, current.content_hashes),
        ("max id", baseline.max_ids, current.max_ids),
    ):
        for subject in sorted(set(before) | set(after)):
            expected = before.get(subject, "<table absent>")
            found = after.get(subject, "<table absent>")
            if expected != found:
                drift.append(Drift(cycle, kind, subject, str(found), str(expected)))

    baseline_sequences = {(s.schema, s.name): s for s in baseline.sequences}
    current_sequences = {(s.schema, s.name): s for s in current.sequences}
    for key in sorted(set(baseline_sequences) | set(current_sequences)):
        expected_seq = baseline_sequences.get(key)
        found_seq = current_sequences.get(key)
        if expected_seq != found_seq:
            drift.append(
                Drift(
                    cycle=cycle,
                    kind="sequence",
                    subject=".".join(key),
                    expected=_describe(found_seq),
                    found=_describe(expected_seq),
                )
            )

    return drift


def _describe(sequence: SequenceValue | None) -> str:
    if sequence is None:
        return "<sequence absent>"
    return f"{sequence.value} (is_called={sequence.is_called})"


def _max_id(connection: psycopg.Connection[tuple[object, ...]], table: str) -> int | None:
    """The table's highest id, or `None` if it has no `id` column.

    Asked of the catalogue rather than attempted and caught, because a failed
    statement aborts the surrounding transaction in Postgres and every
    subsequent query in this capture would then fail for an unrelated reason.
    """
    has_id = _scalar(
        connection,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = 'id'",
        (table,),
    )
    if not int(str(has_id)):
        return None
    value = _scalar(connection, f'SELECT max(id) FROM "{table}"')
    return None if value is None else int(str(value))


def _scalar(
    connection: psycopg.Connection[tuple[object, ...]],
    query: str,
    params: tuple[object, ...] = (),
) -> object:
    row = connection.execute(query, params).fetchone()
    if row is None:  # pragma: no cover - an aggregate always returns a row
        message = f"expected one row from {query!r} and got none"
        raise VerificationError(message)
    return row[0]


def _scalar_int(
    connection: psycopg.Connection[tuple[object, ...]],
    query: str,
) -> int:
    return int(str(_scalar(connection, query)))
