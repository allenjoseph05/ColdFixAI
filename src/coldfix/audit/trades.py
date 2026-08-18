"""What the improvement cost, including the bills nobody wrote a guard for.

Epic 11, S-11.4. *Checks the global resource envelope, not just declared guard
pairs. Reports what increased alongside what decreased.*

**This story depends on S-3.8 and on nothing else in the epic**, which is the one
place Epic 11 branches: every other story hangs off S-11.1's isolated context
because it asks a model something. This asks arithmetic of two measurements, so
there is nothing to isolate it from.

**`08-audit.md` F10 is the whole argument, and it is about denylists.** A guard
pair catches the trade somebody predicted — queries against rows, because someone
knew halving one can explode the other — and catches nothing else. `CostClaim`
requires at least one guard, and that requirement is the strongest thing a
falsification test can do about a trade: it makes the Surgeon name what it thinks
it might break. **What it cannot do is make the Surgeon name what it did not
think of**, and a patch is written by the party with the least interest in
listing that.

So the declared guards are evaluated *and* the envelope is checked, and the
number this module exists to produce is the one in between: **an envelope breach
on a resource no declared guard covers.** That is F10 stated as an observation
rather than a warning — the guards passed, and something still got worse.

**S-3.8 flags rises and never falls, and that is right there and wrong here.**
Its `compare` is a verdict: a two-sided check would flag every successful patch
for the improvement it was written to make. This is a *report*, and AC 2 asks for
both directions, because a rise on its own is not a trade. **A resource that rose
with nothing falling beside it is a regression**, which is a different sentence to
put in front of a human than *it bought its speed with memory* — and an audit
that printed only the rises makes the two indistinguishable.

**Unmeasured is not within tolerance.** S-3.8 already refuses to let a metric it
could not read pass quietly, and this carries that into `clean`: an audit that
never saw peak RSS has not cleared the memory trade. In the Linux sandbox every
envelope metric reads, so this only bites on the development host — which is
exactly the run whose result should not be trusted. S-11.2's `survived` and
S-11.3's `complete`, for the third time in the epic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.primitives.envelope import (
    DEFAULT_TOLERANCES,
    ENVELOPE,
    Availability,
    Breach,
    EnvelopeSample,
    GuardReport,
)
from coldfix.primitives.envelope import compare as compare_envelope
from coldfix.repair.falsification import CostClaim, Guard

RESIDUE = (
    "The envelope is fixed and the domain metrics are whatever the adapter counts, so "
    "this reports every trade that lands on something being watched. A cost paid "
    "somewhere neither list reaches — a queue in another service, a table that will be "
    "vacuumed later, a lock somebody else waits on — shows up nowhere here. "
    "`08-audit.md` F10 is that guard pairs are a denylist; the envelope is a longer "
    "list and is still a list."
)


class TradeError(Exception):
    """The trade audit could not be carried out."""


class Direction(StrEnum):
    """Which way one metric moved between the two revisions."""

    FELL = "fell"
    ROSE = "rose"
    UNCHANGED = "unchanged"
    UNMEASURED = "not measured on both revisions"


@dataclass(frozen=True)
class Movement:
    """One metric, before and after. **Both directions, which is AC 2's half.**"""

    metric: str
    before: float | None
    after: float | None

    @property
    def direction(self) -> Direction:
        if self.before is None or self.after is None:
            return Direction.UNMEASURED
        if self.after < self.before:
            return Direction.FELL
        if self.after > self.before:
            return Direction.ROSE
        return Direction.UNCHANGED

    @property
    def ratio(self) -> float | None:
        """How many times larger. `None` where it came from nothing — reporting
        `inf` would put a number meaning *undefined* into a report."""
        if self.before is None or self.after is None or self.before == 0:
            return None
        return self.after / self.before

    def describe(self) -> str:
        if self.before is None or self.after is None:
            return f"{self.metric}: {Direction.UNMEASURED.value}"
        unit = f" {ENVELOPE[self.metric].unit}" if self.metric in ENVELOPE else ""
        ratio = self.ratio
        scale = f" ({ratio:.2g}x)" if ratio is not None and ratio not in (1.0,) else ""
        return (
            f"{self.metric}: {self.before:g} -> {self.after:g}{unit}{scale} "
            f"— {self.direction.value}"
        )


@dataclass(frozen=True)
class Trade:
    """What was paid, and what it bought. **AC 2 in a type.**

    Holding both sides is the point. S-3.8 is right to flag only rises, because a
    flag is a verdict and a patch exists to make something smaller. A *report*
    that showed only rises could not tell a trade from a plain regression, and
    those two sentences send a reader somewhere different.
    """

    fell: tuple[Movement, ...]
    rose: tuple[Movement, ...]

    @property
    def is_a_trade(self) -> bool:
        """Whether anything was actually bought with what was spent."""
        return bool(self.fell) and bool(self.rose)

    @property
    def is_a_regression(self) -> bool:
        """Something rose and nothing fell. Not a trade — a bill with no purchase."""
        return bool(self.rose) and not self.fell

    def describe(self) -> str:
        if not self.rose:
            fell = ", ".join(item.metric for item in self.fell) or "nothing"
            return f"  Nothing rose. What fell: {fell}."
        lines = ["  What rose:"]
        lines.extend(f"    + {item.describe()}" for item in self.rose)
        if self.fell:
            lines.append("  What it bought:")
            lines.extend(f"    - {item.describe()}" for item in self.fell)
        else:
            lines.append(
                "  **And nothing fell beside it.** That is not a trade, it is a regression: "
                "the patch cost something and bought nothing measured here."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class GuardOutcome:
    """One declared guard, and whether the patched run stayed inside it."""

    guard: Guard
    measured: float | None

    @property
    def held(self) -> bool | None:
        """`None` where the metric was not measured on the patched run.

        Not `True`. A guard nobody could evaluate is the denylist failing in the
        quietest possible way — the metric somebody *did* think of, and still no
        answer about it.
        """
        return None if self.measured is None else self.measured <= self.guard.at_most

    def describe(self) -> str:
        if self.measured is None:
            return (
                f"{self.guard.metric}: declared, allowed up to {self.guard.at_most:g}, "
                "and never measured on the patched run"
            )
        verdict = "held" if self.held else "BROKEN"
        return (
            f"{self.guard.metric}: {self.guard.baseline:g} -> {self.measured:g}, "
            f"allowed up to {self.guard.at_most:g} — {verdict}"
        )


@dataclass(frozen=True)
class TradeAudit:
    """Everything that moved, what was declared, and what nobody declared.

    The three parts answer three different questions, and the third is the one
    this story exists for: the guards say what the Surgeon thought could break,
    the envelope says what actually got worse, and `uncovered` is the gap between
    them.
    """

    claim: CostClaim
    trade: Trade
    guards: tuple[GuardOutcome, ...]
    envelope: GuardReport
    cost: Movement

    def __post_init__(self) -> None:
        declared = {guard.metric for guard in self.claim.guards}
        evaluated = {outcome.guard.metric for outcome in self.guards}
        if declared != evaluated:
            missing = sorted(declared - evaluated)
            message = (
                f"the claim declares guards on {sorted(declared)} and this audit reports on "
                f"{sorted(evaluated)}. A declared guard dropped from the report is the one kind "
                f"of trade somebody did predict, going unanswered. Missing: {missing}"
            )
            raise TradeError(message)

    @property
    def broken_guards(self) -> tuple[GuardOutcome, ...]:
        """Declared guards the patched run exceeded."""
        return tuple(outcome for outcome in self.guards if outcome.held is False)

    @property
    def unevaluated_guards(self) -> tuple[GuardOutcome, ...]:
        return tuple(outcome for outcome in self.guards if outcome.held is None)

    @property
    def uncovered(self) -> tuple[Breach, ...]:
        """**The number this module exists to produce.**

        Envelope resources that rose past tolerance and that no declared guard was
        watching. `08-audit.md` F10 as an observation rather than a warning: the
        guard pairs passed, and something still got worse. Usually this is every
        breach, because guards are declared on domain metrics and the envelope is
        global — and *usually all of them* is the finding, not an artifact.
        """
        declared = {guard.metric for guard in self.claim.guards}
        return tuple(breach for breach in self.envelope.breaches if breach.metric not in declared)

    @property
    def unmeasured(self) -> Mapping[str, Availability]:
        return self.envelope.unmeasured

    @property
    def complete(self) -> bool:
        """Whether every question this audit is supposed to answer was answerable."""
        return not self.unmeasured and not self.unevaluated_guards

    @property
    def clean(self) -> bool:
        """No declared guard broken, nothing in the envelope risen, and everything
        actually checked.

        The third condition is the one an obvious implementation drops, and on
        this host it is the one that fires: three envelope metrics need `/proc` or
        `getrusage`, so a Windows run checks five of eight and would otherwise
        report a clean envelope it could not see half of.
        """
        return not self.broken_guards and not self.envelope.flagged and self.complete

    def describe(self) -> str:
        lines = [
            f"TRADE AUDIT — {len(self.broken_guards)} declared guards broken, "
            f"{len(self.envelope.breaches)} envelope resources risen "
            f"({len(self.uncovered)} of them undeclared).",
            f"  What it was for: {self.cost.describe()}",
        ]
        if self.cost.direction is not Direction.FELL:
            lines.append(
                "  **The cost metric did not fall.** Whatever else moved, it was not paid for "
                "by the improvement this patch was written to make."
            )

        lines.append("  Declared guards:")
        lines.extend(f"    {outcome.describe()}" for outcome in self.guards)

        if self.uncovered:
            lines.append(
                "  **Risen, and on no declared guard.** `08-audit.md` F10: a guard pair catches "
                "the trade somebody predicted. These are the ones nobody did."
            )
            lines.extend(f"    ! {breach}" for breach in self.uncovered)

        lines.extend(self.trade.describe().splitlines())

        if self.unmeasured:
            unread = ", ".join(
                f"{name} ({why.value})" for name, why in sorted(self.unmeasured.items())
            )
            lines.append(
                f"  **{len(self.unmeasured)} envelope resources were never read** ({unread}), so "
                "this covers less than a sandbox run would. Not within tolerance — unseen."
            )
        if self.unevaluated_guards:
            names = ", ".join(item.guard.metric for item in self.unevaluated_guards)
            lines.append(
                f"  **{len(self.unevaluated_guards)} declared guards could not be evaluated** "
                f"({names}) — the trades somebody did predict, still unanswered."
            )
        if self.clean:
            lines.append(
                "  Nothing declared was broken and nothing in the envelope rose. That is a null "
                "result and it ships as one."
            )
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)


def audit_trades(  # noqa: PLR0913 - the two envelope samples, the two sets of
    # domain metrics, the claim and the tolerances are six independent facts.
    # The envelope and the domain measurements are deliberately not one mapping:
    # see `_movements`.
    *,
    before: EnvelopeSample,
    after: EnvelopeSample,
    domain_before: Mapping[str, float],
    domain_after: Mapping[str, float],
    claim: CostClaim,
    tolerances: Mapping[str, float] = DEFAULT_TOLERANCES,
) -> TradeAudit:
    """Check the declared guards and the whole envelope, and report both directions.

    AC 1 is `compare_envelope`, reused rather than reimplemented — S-3.8 owns the
    tolerances, the absolute floors and the availability reporting, and a second
    copy of the rule *a rise must clear a ratio and a floor* would be two answers
    to a question with one right one.

    AC 2 is `Trade`, which is this story's own: S-3.8 records only rises because a
    flag is a verdict, and a report that showed only rises could not separate a
    trade from a regression.

    Raises:
        TradeError: the claim's cost metric was not measured on both revisions,
            so there is no improvement for anything to have been traded against.
    """
    cost = Movement(
        metric=claim.metric,
        before=domain_before.get(claim.metric),
        after=domain_after.get(claim.metric),
    )
    if cost.direction is Direction.UNMEASURED:
        message = (
            f"{claim.metric!r} is the metric this patch claims to improve and it was not "
            "measured on both revisions. Every trade here is a cost paid for that improvement, "
            "and without it there is nothing to weigh anything against"
        )
        raise TradeError(message)

    guards = tuple(
        GuardOutcome(guard=guard, measured=domain_after.get(guard.metric)) for guard in claim.guards
    )
    report = compare_envelope(before, after, tolerances=tolerances)
    moved = _movements(
        before=before, after=after, domain_before=domain_before, domain_after=domain_after
    )
    trade = Trade(
        fell=tuple(item for item in moved if item.direction is Direction.FELL),
        rose=tuple(item for item in moved if item.direction is Direction.ROSE),
    )
    return TradeAudit(claim=claim, trade=trade, guards=guards, envelope=report, cost=cost)


def _movements(
    *,
    before: EnvelopeSample,
    after: EnvelopeSample,
    domain_before: Mapping[str, float],
    domain_after: Mapping[str, float],
) -> tuple[Movement, ...]:
    """Every metric that moved, from both sources, in one list.

    **The two sources stay separate as arguments and merge only here**, because a
    single mapping would let a domain counter named `wall_seconds` overwrite the
    envelope's — silently, and in the direction of the patch looking better, since
    a domain timer measures the window and the envelope measures the process.

    A name in both is reported twice, prefixed, rather than one shadowing the
    other: two things called the same thing were measured, and which of them a
    reader wanted is not this function's guess to make.
    """
    moved: list[Movement] = []
    for metric in sorted(ENVELOPE):
        moved.append(Movement(metric=metric, before=before[metric], after=after[metric]))

    overlap = set(ENVELOPE) & (domain_before.keys() | domain_after.keys())
    for metric in sorted(domain_before.keys() | domain_after.keys()):
        name = f"workload.{metric}" if metric in overlap else metric
        moved.append(
            Movement(
                metric=name,
                before=domain_before.get(metric),
                after=domain_after.get(metric),
            )
        )
    return tuple(moved)


def uncovered_by(claim: CostClaim, breaches: Sequence[Breach]) -> tuple[str, ...]:
    """Which risen resources no declared guard was watching, by name.

    Exposed separately so a caller can ask F10's question without building an
    audit — and so a test can assert the set rather than a rendering.
    """
    declared = {guard.metric for guard in claim.guards}
    return tuple(breach.metric for breach in breaches if breach.metric not in declared)
