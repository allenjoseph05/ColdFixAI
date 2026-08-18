"""Asking whether the improvement is real, and refusing to guess where it cannot tell.

Epic 11, S-11.3. *Checks for cached state across runs, deferred work,
over-fetching, stubbed responses, shape-specific special-casing. Verifies the
improvement survives a fresh process.*

**The five classes are not defined here.** S-10.1 built `Cheat` as an enum rather
than `03-agents.md` §5.4's `list[str]` for exactly this story — *S-11.3 has to ask
could a cheat of class X pass this test and needs the same vocabulary to ask it
in* — so this module imports it. A second spelling of the five would be two
vocabularies that drift, and the one place they must agree is the join between a
test that says what it catches and an audit that says what it found.

**This module never measures anything.** `CLAUDE.md` puts the measuring in the
harness, and S-9.6 records why an auditor that produced its own numbers would be
the one place that rule could not be enforced. It is handed a `Measure` and it
decides what to ask for; the harness decides how to run it and what it costs.

**A question that could not be asked is not a question that came back clean.**
This is the whole shape of the module. Four of the five classes need a metric
that only some adapters produce — a row count, a byte count, a whole-process
total — and the obvious implementation quietly skips the ones it lacks and
reports the rest as passing. That reads as *five checks, nothing found* and means
*one check, nothing found*. So every class returns `DETECTED`, `NOT_DETECTED` or
**`UNTESTED`**, `CheatAudit` refuses to be built unless all five appear, and
`clean` is false while any of them is untested. S-3.1's *no* against *not known*,
and S-11.2's `survived` in the same shape one story on.

**Cold is the first pass through a fresh process, and that is what AC 2 needs.**
`primitives/measurement.py` already names the hazard: *work the first run warmed
is free for the second, so the second looks cheaper than it is*. A `Reading` is
therefore one fresh process driven more than once, keeping the cold pass apart
from the ones that followed it — which is one container run per revision and
answers both halves. The improvement warm is `original` against `patched` on the
repeated passes; the improvement **cold** is the same comparison on the first
pass, and a patch whose gain exists only in the first is a patch that does not
survive a fresh process.

**The original is the control for warming, because every framework warms.** Django
fills a connection pool, compiles templates and populates an app registry on the
first request through any codebase. An audit that read *the second run was faster*
as cached state would report a cheat on every patch it ever saw. The signal is
that the **patch increased** the warm-up, not that a warm-up exists.

**What counts as a real move is not decided here either.** S-9.6's rule applies
unchanged: a count is exact and reproduces to the integer, so a count that moved
at all is material; a duration is one sample against a floor S-0.4 measured at
about 12%, so only a move beyond that is evidence. Re-deriving thresholds would
be a second answer to a question this project has already answered once.

**Accusing a correct patch is the expensive error here.** S-11.2 recorded it: an
objection sends the Surgeon to rewrite code that was right, and it costs a repair
cycle to discover the objection was noise. So every comparison that could go
either way requires the move to clear the floor before it is reported.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from coldfix.audit.equivalence import Equivalence
from coldfix.audit.scales import MEASURED_DRIFT
from coldfix.primitives.measurement import MetricKind
from coldfix.primitives.scaling import Distribution
from coldfix.repair.falsification import Cheat

RESIDUE = (
    "Every class here is answered by a number the harness took, so this audit sees "
    "exactly as far as the counters reach. A cheat that moves work somewhere nothing "
    "counts — into a thread, a signal handler, another service, the operating system's "
    "page cache — improves every metric on this list and is invisible to all of them. "
    "`UNTESTED` names the questions nobody asked; there is no label for the ones "
    "nobody thought to instrument."
)


class CheatError(Exception):
    """The cheat audit could not be carried out."""


class Revision(StrEnum):
    """Which of the two revisions a reading was taken from."""

    ORIGINAL = "the revision before the change"
    PATCHED = "the revision with the change"


class Finding(StrEnum):
    """What one class established. Three, and the third is why this is not a boolean.

    Collapsing `UNTESTED` into `NOT_DETECTED` turns *nobody looked* into *nothing
    there*, which is the reading that ships a patch on the strength of an audit
    that could not run.
    """

    DETECTED = "found"
    NOT_DETECTED = "looked for and not found"
    UNTESTED = "not looked for, because the measurement it needs was not taken"


@dataclass(frozen=True)
class Reading:
    """One fresh process, driven more than once, with the cold pass kept apart.

    `first` is the pass that paid for whatever the process had not yet warmed.
    `repeated` are the passes after it **in the same process**, which is what
    makes the pair able to see state carried across runs — two fresh processes
    could not, because neither would be the second run of anything.

    `repeated` may be empty. That is a harness that ran the workload once, and it
    leaves the cached-state question `UNTESTED` rather than answered.
    """

    revision: Revision
    shape: Distribution
    first: Mapping[str, float]
    repeated: tuple[Mapping[str, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.first:
            message = (
                f"the {self.revision.value} reading measured nothing on its first pass. An "
                "audit handed one would find no cheat in it and report that as the patch "
                "being honest"
            )
            raise CheatError(message)

    def cold(self, metric: str) -> float | None:
        """What the metric cost on the pass that warmed nothing."""
        return self.first.get(metric)

    def warm(self, metric: str) -> float | None:
        """The median of the passes that followed, or `None` if there were none.

        Median rather than mean because a single slow pass — a garbage collection,
        a neighbour on the machine — should not decide whether a patch is accused
        of caching.
        """
        values = [run[metric] for run in self.repeated if metric in run]
        return statistics.median(values) if values else None

    def warm_up(self, metric: str) -> float | None:
        """The fraction of the cold cost that the later passes did not pay.

        `None` where there is nothing to compare, and where the cold pass cost
        zero — a fraction of nothing is a division nobody can read, and reporting
        it would put an undefined number into an accusation.
        """
        cold, warm = self.cold(metric), self.warm(metric)
        if cold is None or warm is None or cold == 0:
            return None
        return (cold - warm) / abs(cold)


type Measure = Callable[[Revision, Distribution], Reading]
"""What the harness does: run the workload in a fresh process, more than once,
and hand back what it measured.

A callable rather than a stored table for S-1.6's reason — a comparison against
numbers somebody saved earlier is a comparison against a different program."""


@dataclass(frozen=True)
class Metrics:
    """Which measured names answer which question.

    Named by the caller because only the adapter knows what its own counters are
    called, and **`None` is the honest answer** where an adapter has no such
    counter. Every `None` here becomes an `UNTESTED` class rather than a check
    that quietly passes.
    """

    cost: str
    """The metric the patch claims to reduce."""

    kinds: Mapping[str, MetricKind]
    """Count or duration, per metric. Decides what counts as a real move — S-9.6's
    rule, imported rather than restated."""

    calls: str | None = None
    """A call count, `db.query` and its kin. Over-fetch is *fewer calls*..."""

    work: str | None = None
    """...and this is *each returning more*: rows, bytes, objects. The
    non-negotiable in two field names — queries down while rows explode is not an
    improvement, and neither name alone can say so."""

    whole_process: str | None = None
    """A total covering more than the measured window. Deferred work is invisible
    without one, because moving a cost outside the window improves the window."""

    response_size: str | None = None
    """Bytes or fields returned. A stubbed response is a smaller one."""

    def __post_init__(self) -> None:
        """Every metric named here has a declared kind, checked once at the top.

        **Found by a test that expected `detect` to raise and watched it succeed.**
        With the check only inside `kind_of`, a configuration naming a cost metric
        whose kind nobody declared produced a complete audit — every class
        `UNTESTED` for want of *other* metrics, so nothing ever asked what rule the
        cost metric moved under — and the error surfaced later from a property, on
        a `CheatAudit` that had already been built and could already be read.
        """
        undeclared = sorted(
            metric
            for metric in (
                self.cost,
                self.calls,
                self.work,
                self.whole_process,
                self.response_size,
            )
            if metric is not None and metric not in self.kinds
        )
        if undeclared:
            message = (
                f"no kind declared for {undeclared}, so there is no rule for what a move in "
                f"them means — a count is exact and a duration is one sample, and the two "
                f"disagree about every small move. Declared: {sorted(self.kinds)}"
            )
            raise CheatError(message)

    def kind_of(self, metric: str) -> MetricKind:
        """Raises rather than defaulting, for the reason `__post_init__` states."""
        try:
            return self.kinds[metric]
        except KeyError:
            message = (
                f"no kind declared for {metric!r}, so there is no rule for what a move in it "
                f"means. Declared: {sorted(self.kinds)}"
            )
            raise CheatError(message) from None


@dataclass(frozen=True)
class Check:
    """One class, what was concluded, and the numbers it was concluded from."""

    cheat: Cheat
    finding: Finding
    reason: str
    numbers: Mapping[str, float] = field(default_factory=dict)
    """What was read, so a reader can redo the arithmetic rather than trust it."""

    def describe(self) -> str:
        read = ", ".join(f"{name}={value:g}" for name, value in sorted(self.numbers.items()))
        return f"  {self.cheat.name.lower()}: {self.finding.value} — {self.reason}" + (
            f" [{read}]" if read else ""
        )


@dataclass(frozen=True)
class CheatAudit:
    """Whether the improvement is real, and how much of that question was asked.

    **All five classes are present or this cannot be built.** A class that went
    missing would shorten the list while every report still called it a cheat
    audit — the construction S-10.1 used for `catches`, enforced here in the
    constructor because this is the artifact a verdict reads.
    """

    metrics: Metrics
    checks: tuple[Check, ...]
    original: Reading
    patched: Reading
    relative_noise: float

    def __post_init__(self) -> None:
        covered = [check.cheat for check in self.checks]
        if sorted(covered, key=list(Cheat).index) != list(Cheat) or len(covered) != len(Cheat):
            missing = sorted(set(Cheat) - set(covered), key=list(Cheat).index)
            message = (
                f"a cheat audit reporting {len(covered)} of {len(Cheat)} classes is a shorter "
                f"list reading as a complete one. Missing: {[item.name for item in missing]}"
            )
            raise CheatError(message)

    @property
    def detected(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.finding is Finding.DETECTED)

    @property
    def untested(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.finding is Finding.UNTESTED)

    @property
    def complete(self) -> bool:
        """Whether all five questions were actually asked."""
        return not self.untested

    @property
    def survives_a_fresh_process(self) -> bool | None:
        """**AC 2.** `None` where the question could not be asked.

        The improvement measured on the repeated passes against the improvement
        measured on the cold one. A gain that exists warm and not cold is a gain
        the second run inherited from the first, and a fresh process is where a
        real user arrives.

        Not a boolean with `False` for *unknown*: an audit that reported an
        unaskable question as a failure would block every patch on a harness that
        ran the workload once.
        """
        cost = self.metrics.cost
        kind = self.metrics.kind_of(cost)
        warm_gain = _improved(
            kind, self.original.warm(cost), self.patched.warm(cost), self.relative_noise
        )
        if warm_gain is None:
            return None
        cold_gain = _improved(
            kind, self.original.cold(cost), self.patched.cold(cost), self.relative_noise
        )
        if cold_gain is None:
            return None
        return cold_gain if warm_gain else True

    @property
    def clean(self) -> bool:
        """Whether the improvement survived every question that was asked — and
        whether every question was asked.

        Three conditions, and the second is the one an obvious implementation
        drops. *Five checks, nothing found* and *one check and four it could not
        run, nothing found* are different answers, and only the first is a null
        result worth shipping.
        """
        return not self.detected and self.complete and self.survives_a_fresh_process is not False

    def describe(self) -> str:
        cost = self.metrics.cost
        lines = [
            f"CHEAT AUDIT on {cost} against a {self.relative_noise:.0%} noise floor — "
            f"{len(self.detected)} found, {len(self.untested)} not asked.",
            f"  cold: {_show(self.original.cold(cost))} -> {_show(self.patched.cold(cost))}   "
            f"warm: {_show(self.original.warm(cost))} -> {_show(self.patched.warm(cost))}",
        ]
        lines.extend(check.describe() for check in self.checks)

        survives = self.survives_a_fresh_process
        if survives is False:
            lines.append(
                "  **The improvement does not survive a fresh process.** It is there on the "
                "repeated passes and gone on the first, so what was measured is the second run "
                "of something rather than a cheaper program."
            )
        elif survives is None:
            lines.append(
                "  The workload was driven once per process, so there is nothing to compare a "
                "cold pass against and this question was not asked."
            )

        if not self.complete:
            names = ", ".join(check.cheat.name.lower() for check in self.untested)
            lines.append(
                f"  **{len(self.untested)} of {len(Cheat)} classes were never checked** "
                f"({names}). They were not looked for and found absent; the measurement each "
                "needs was not taken."
            )
        elif self.clean:
            lines.append(
                "  All five classes were checked and none was found. That is a null result and "
                "it ships as one."
            )
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)


def detect(  # noqa: PLR0913 - the measure, the metric names, the fixture shape,
    # the alternative shapes, an equivalence attack and the noise floor are six
    # independent facts. Bundling them would invent a type whose only purpose is
    # to be unpacked one line later.
    measure: Measure,
    *,
    metrics: Metrics,
    shape: Distribution,
    alternatives: Sequence[Distribution] = (),
    equivalence: Equivalence | None = None,
    relative_noise: float = MEASURED_DRIFT,
) -> CheatAudit:
    """Ask all five questions, and say which of them could not be asked. AC 1 and AC 2.

    Costs two runs, plus two for every alternative shape. `alternatives` is what
    makes the shape-specific class answerable at all: a special case for the
    fixture cannot be seen from the fixture it was written for, and with none
    supplied that class comes back `UNTESTED` rather than clean.

    `equivalence` is S-11.2's result where one was run. A stubbed response is a
    changed response, and that story already establishes it against real payloads
    with a reproducing input — a size comparison here is the weaker signal and is
    used only when the stronger one is absent.

    Raises:
        CheatError: a metric was compared without a declared kind, or a reading
            came back empty, or the shape a reading claims is not the one asked
            for.
    """
    original = _read(measure, Revision.ORIGINAL, shape)
    patched = _read(measure, Revision.PATCHED, shape)

    elsewhere = {
        other: (_read(measure, Revision.ORIGINAL, other), _read(measure, Revision.PATCHED, other))
        for other in alternatives
    }

    checks = (
        _cached_state(original, patched, metrics, relative_noise),
        _deferred_work(original, patched, metrics, relative_noise),
        _over_fetch(original, patched, metrics, relative_noise),
        _stubbed_response(original, patched, metrics, relative_noise, equivalence),
        _shape_specific(
            original, patched, metrics, relative_noise, shape=shape, elsewhere=elsewhere
        ),
    )
    return CheatAudit(
        metrics=metrics,
        checks=checks,
        original=original,
        patched=patched,
        relative_noise=relative_noise,
    )


def _read(measure: Measure, revision: Revision, shape: Distribution) -> Reading:
    """Take one reading and check it is the one that was asked for.

    A harness that ignored an argument would return the same numbers for both
    revisions, and the audit would report every class absent — a patch cleared by
    a measurement that never distinguished it from the original.
    """
    reading = measure(revision, shape)
    if reading.revision is not revision or reading.shape is not shape:
        message = (
            f"asked for {revision.name} at {shape.value} and got {reading.revision.name} at "
            f"{reading.shape.value}. A reading that is not of what was requested would be "
            "compared against the wrong thing and the comparison would look ordinary"
        )
        raise CheatError(message)
    return reading


def _cached_state(original: Reading, patched: Reading, metrics: Metrics, noise: float) -> Check:
    """Did the patch make the second run cheaper than the first, more than the original did?

    **The original is the control, and without it this check is useless.** Every
    framework warms: a connection pool fills, templates compile, an app registry
    populates. Reading *the later passes were faster* as cached state would
    accuse every patch ever measured, so what is reported is the patch's warm-up
    **in excess of** the original's.
    """
    cost = metrics.cost
    before, after = original.warm_up(cost), patched.warm_up(cost)
    if before is None or after is None:
        return Check(
            cheat=Cheat.CACHED_STATE,
            finding=Finding.UNTESTED,
            reason=(
                "the workload was driven once per process, so there is no second run to be "
                "cheaper than the first"
            ),
        )

    numbers = {"warm_up_original": before, "warm_up_patched": after, "excess": after - before}
    if after - before > noise:
        return Check(
            cheat=Cheat.CACHED_STATE,
            finding=Finding.DETECTED,
            reason=(
                f"the patched revision's later passes are {after:.0%} cheaper than its first "
                f"against the original's {before:.0%}, so the change carries state from one run "
                "into the next"
            ),
            numbers=numbers,
        )
    return Check(
        cheat=Cheat.CACHED_STATE,
        finding=Finding.NOT_DETECTED,
        reason=(
            f"the patch warms up {after:.0%} against the original's {before:.0%}, which is "
            "inside the floor and is the framework doing what it does on any code"
        ),
        numbers=numbers,
    )


def _deferred_work(original: Reading, patched: Reading, metrics: Metrics, noise: float) -> Check:
    """Did the measured window get cheaper without the process getting cheaper?"""
    if metrics.whole_process is None:
        return Check(
            cheat=Cheat.DEFERRED_WORK,
            finding=Finding.UNTESTED,
            reason=(
                "no whole-process metric was measured, and work moved out of the window is "
                "invisible from inside it — the window improves either way"
            ),
        )

    cost, total = metrics.cost, metrics.whole_process
    window = _improved(metrics.kind_of(cost), original.cold(cost), patched.cold(cost), noise)
    everything = _improved(metrics.kind_of(total), original.cold(total), patched.cold(total), noise)
    if window is None or everything is None:
        return Check(
            cheat=Cheat.DEFERRED_WORK,
            finding=Finding.UNTESTED,
            reason=f"{cost} or {total} was missing from one of the two cold passes",
        )

    numbers = _numbers(original, patched, (cost, total))
    if window and not everything:
        return Check(
            cheat=Cheat.DEFERRED_WORK,
            finding=Finding.DETECTED,
            reason=(
                f"{cost} improved while {total} did not, so the work left the measured window "
                "rather than the program"
            ),
            numbers=numbers,
        )
    return Check(
        cheat=Cheat.DEFERRED_WORK,
        finding=Finding.NOT_DETECTED,
        reason=(
            f"{total} moved with {cost}, so what left the window left the process"
            if window
            else f"{cost} did not improve on a cold pass, so there is no gain to have been moved"
        ),
        numbers=numbers,
    )


def _over_fetch(original: Reading, patched: Reading, metrics: Metrics, noise: float) -> Check:
    """Fewer calls, each returning more. **The guard-counter non-negotiable.**

    `CLAUDE.md`: *queries down while rows explode is not an improvement.* Neither
    number says it alone, which is why this class needs two metric names and is
    `UNTESTED` without both — a harness counting only queries would report the
    improvement and never see what paid for it.
    """
    if metrics.calls is None or metrics.work is None:
        missing = "a call count" if metrics.calls is None else "a measure of work returned"
        return Check(
            cheat=Cheat.OVER_FETCH,
            finding=Finding.UNTESTED,
            reason=(
                f"{missing} was not measured. Queries down while rows explode is not an "
                "improvement, and one of the two numbers cannot say so"
            ),
        )

    calls, work = metrics.calls, metrics.work
    fewer = _improved(metrics.kind_of(calls), original.cold(calls), patched.cold(calls), noise)
    more = _worsened(metrics.kind_of(work), original.cold(work), patched.cold(work), noise)
    if fewer is None or more is None:
        return Check(
            cheat=Cheat.OVER_FETCH,
            finding=Finding.UNTESTED,
            reason=f"{calls} or {work} was missing from one of the two cold passes",
        )

    numbers = _numbers(original, patched, (calls, work))
    if fewer and more:
        return Check(
            cheat=Cheat.OVER_FETCH,
            finding=Finding.DETECTED,
            reason=(
                f"{calls} fell and {work} rose, which is the same work fetched in fewer, larger "
                "pieces rather than less work"
            ),
            numbers=numbers,
        )
    return Check(
        cheat=Cheat.OVER_FETCH,
        finding=Finding.NOT_DETECTED,
        reason=f"{work} did not rise as {calls} fell",
        numbers=numbers,
    )


def _stubbed_response(
    original: Reading,
    patched: Reading,
    metrics: Metrics,
    noise: float,
    equivalence: Equivalence | None,
) -> Check:
    """Does the response still contain what it used to?

    **S-11.2 answers this better and is used first.** That story drives real
    payloads through both revisions and comes back with a reproducing input; a
    size comparison is a proxy, and a proxy that disagreed with the real
    comparison would be the weaker number overruling the stronger one.
    """
    if equivalence is not None and equivalence.reproducing:
        return Check(
            cheat=Cheat.STUBBED_RESPONSE,
            finding=Finding.DETECTED,
            reason=(
                f"the equivalence attack found {len(equivalence.reproducing)} inputs where the "
                f"two revisions disagree — first: {equivalence.reproducing[0].summary}"
            ),
        )

    if metrics.response_size is None:
        if equivalence is not None and equivalence.survived:
            return Check(
                cheat=Cheat.STUBBED_RESPONSE,
                finding=Finding.NOT_DETECTED,
                reason=(
                    f"the equivalence attack compared {len(equivalence.compared)} inputs and "
                    "found no difference, which is a stronger answer than a size would be"
                ),
            )
        return Check(
            cheat=Cheat.STUBBED_RESPONSE,
            finding=Finding.UNTESTED,
            reason=(
                "no response size was measured and no equivalence attack settled it, so nothing "
                "here has looked at what the response contains"
            ),
        )

    size = metrics.response_size
    smaller = _improved(metrics.kind_of(size), original.cold(size), patched.cold(size), noise)
    if smaller is None:
        return Check(
            cheat=Cheat.STUBBED_RESPONSE,
            finding=Finding.UNTESTED,
            reason=f"{size} was missing from one of the two cold passes",
        )

    numbers = _numbers(original, patched, (size,))
    if smaller:
        return Check(
            cheat=Cheat.STUBBED_RESPONSE,
            finding=Finding.DETECTED,
            reason=(
                f"{size} fell, so the response carries less than it did — cheaper because there "
                "is less of it, which is not the same as cheaper to produce"
            ),
            numbers=numbers,
        )
    return Check(
        cheat=Cheat.STUBBED_RESPONSE,
        finding=Finding.NOT_DETECTED,
        reason=f"{size} did not fall",
        numbers=numbers,
    )


def _shape_specific(  # noqa: PLR0913 - the pair measured here, the metric names,
    # the floor, the shape they were taken at and the pairs taken elsewhere. The
    # shape is not derivable from the readings: they carry the one they were taken
    # at, and this needs the one that was *asked* for, which is what `_read` checks.
    original: Reading,
    patched: Reading,
    metrics: Metrics,
    noise: float,
    *,
    shape: Distribution,
    elsewhere: Mapping[Distribution, tuple[Reading, Reading]],
) -> Check:
    """Does the improvement hold on data the patch was not written against?

    **This class cannot be answered from one fixture, and saying so is the point.**
    A special case for the shape that happens to be seeded looks exactly like a
    general fix when measured on that shape — S-9.3's argument, arriving two epics
    later at the artifact it warned about.
    """
    if not elsewhere:
        return Check(
            cheat=Cheat.SHAPE_SPECIFIC,
            finding=Finding.UNTESTED,
            reason=(
                f"only {shape.value} data was measured. A special case for the fixture's shape "
                "is invisible from that shape, so this is unasked rather than absent"
            ),
        )

    cost = metrics.cost
    kind = metrics.kind_of(cost)
    here = _improved(kind, original.cold(cost), patched.cold(cost), noise)
    if here is None:
        return Check(
            cheat=Cheat.SHAPE_SPECIFIC,
            finding=Finding.UNTESTED,
            reason=f"{cost} was missing from one of the two cold passes at {shape.value}",
        )

    held: dict[Distribution, bool | None] = {
        other: _improved(kind, before.cold(cost), after.cold(cost), noise)
        for other, (before, after) in elsewhere.items()
    }
    lost = sorted(other.value for other, gain in held.items() if gain is False)
    if here and lost:
        return Check(
            cheat=Cheat.SHAPE_SPECIFIC,
            finding=Finding.DETECTED,
            reason=(
                f"{cost} improved on {shape.value} data and not on {', '.join(lost)}, so the "
                "change is a special case for the shape that happened to be seeded"
            ),
            numbers=_numbers(original, patched, (cost,)),
        )
    if any(gain is None for gain in held.values()):
        return Check(
            cheat=Cheat.SHAPE_SPECIFIC,
            finding=Finding.UNTESTED,
            reason=f"{cost} was missing from a cold pass at another shape",
        )
    return Check(
        cheat=Cheat.SHAPE_SPECIFIC,
        finding=Finding.NOT_DETECTED,
        reason=(
            f"the improvement held at {', '.join(sorted(other.value for other in held))} as well"
            if here
            else f"{cost} did not improve at {shape.value} either, so there is no special case"
        ),
        numbers=_numbers(original, patched, (cost,)),
    )


def _improved(
    kind: MetricKind, before: float | None, after: float | None, noise: float
) -> bool | None:
    """Did the metric fall by enough to mean something? `None` where it was not measured.

    S-9.6's rule, applied in one direction: a count is exact and reproduces to the
    integer, so any fall is real; a duration is one sample and only a fall past
    the floor is evidence.
    """
    return _moved(kind, before, after, noise, falling=True)


def _worsened(
    kind: MetricKind, before: float | None, after: float | None, noise: float
) -> bool | None:
    """The same question pointed the other way, for a guard counter."""
    return _moved(kind, before, after, noise, falling=False)


def _moved(
    kind: MetricKind, before: float | None, after: float | None, noise: float, *, falling: bool
) -> bool | None:
    if before is None or after is None:
        return None
    delta = before - after if falling else after - before
    if delta <= 0:
        return False
    if kind is MetricKind.COUNT:
        return True
    if before == 0:
        # Any movement away from zero clears every relative floor, and dividing
        # by it to say so would be arithmetic nobody can check.
        return True
    return delta / abs(before) > noise


def _numbers(original: Reading, patched: Reading, wanted: Sequence[str]) -> dict[str, float]:
    """The cold readings a check used, named so a reader can redo its arithmetic."""
    found: dict[str, float] = {}
    for metric in wanted:
        for label, reading in (("original", original), ("patched", patched)):
            value = reading.cold(metric)
            if value is not None:
                found[f"{metric}_{label}"] = value
    return found


def _show(value: float | None) -> str:
    return "not measured" if value is None else f"{value:g}"
