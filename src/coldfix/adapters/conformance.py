"""The suite an adapter has to pass, shipped so a third party can run it.

Epic 14, S-14.4. `docs/09-adapters.md` is the prose half; this is the executable
one, and it is deliberately in `src/` rather than in `tests/`. A conformance
suite that lives in this repository's test directory is one nobody outside this
repository can run, and the acceptance criterion is that a third party can
implement an adapter — which means they need to be able to *check* it.

**It is a harness, not a pipeline stage.** Nothing under `src/` calls
`run_conformance`; a person does, the way `eval/ablation.py` is driven. That is a
category rather than a gap — see the note on eval harnesses in the backlog.

## What a run can and cannot say

Every check reports `PASSED`, `FAILED`, or `SKIPPED`, and **a skipped check is
not a passed one**. Most of the interesting checks need something the caller has
to supply — a session, a database, a workload that raises counted events — and an
adapter run without any of them will come back with a clean-looking report that
attests almost nothing. `Report.describe` therefore leads with the counts and
says so in words when anything was skipped, and `Report.attested` is the property
to read when what you want to know is *did this run actually check the thing*.

This is `Selection.withheld_notice`'s rule one layer out: an empty result must
read as *these were not run* rather than as *these found nothing*.

## What the checks are for

Several need no environment at all and check the **declarations**: that an
adapter names its framework and ORM, that its hooks are catalogue counters an
adapter is allowed to supply, that its `capabilities()` claims nothing the
harness owns, that its protected paths do not narrow the defaults, and that it
declares framework frames at all.

**Two of those check less than they look like they check**, and the docstrings
say which. `Declarations.patch_policy` concatenates onto the defaults, so an
adapter using it *cannot* narrow them — the check is there for the one route that
remains, a `Declarations` subclass overriding the method. And the frames check
synthesizes its stack from the adapter's *own* first fragment, so it catches an
empty list and a broken hand-off and cannot tell whether the fragments are the
right ones for the framework. A harness that knew what a Flask stack looks like
would be a harness with a framework in it.

The rest need a real subject, and three of them are the ones worth having:

**`run_workload` is checked for self-consistency, because an adapter is the last
place a measurement can be fabricated.** No schema stops an adapter returning
numbers it invented — only the framework knows how to count its own queries, so
the harness cannot second-guess the values. What it *can* do is check the
relationships the caller already knows: the number of samples must equal the
repeats that were asked for, the reported median must be the median of those
samples, and the scale and row counts handed in must come back unchanged. An
adapter that drives once and reports five samples fails here, and so does one
that reports a median it did not compute.

**Reset reliability is S-2.7's `verify`, not a new opinion.** Ten cycles, real
row counts, drift reported per cycle. An adapter offering a mechanism that does
not restore state is the failure that makes every later measurement a measurement
of two runs added together.

**Hook overhead is per event against a stated denominator.** A counter's cost is
fixed per event, so a bare percentage is a budget against an unnamed operation —
`REFERENCE_OPERATION_SECONDS` is what the five percent is five percent of, and it
is ADR 013's measured instrumented database call.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from coldfix.adapters.interface import ADAPTER_CAPABILITIES, FrameworkAdapter, Subject, installed
from coldfix.bench.counting import count
from coldfix.explorer.work import Drive
from coldfix.primitives.counters import (
    DB_QUERY,
    OVERHEAD_BUDGET,
    REFERENCE_OPERATION_SECONDS,
)
from coldfix.primitives.localization import Frame
from coldfix.primitives.registry import Capability
from coldfix.sandbox.modes import CandidateSession
from coldfix.sandbox.patching import DEFAULT_PROTECTED_PATTERNS, ProtectedPathError
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.verification import NoReliableResetError, choose_reset

PROTECTED_PROBE = "tests/test_conformance_probe.py"
"""A path every default policy protects, used to check that a patch is refused.

Chosen to match `**/tests/**` and `**/test_*.py` at once, so the refusal cannot
come from a single over-narrow rule that happens to be right by accident.
"""

DEFAULT_CYCLES = 10
"""Reset cycles. S-2.7's number, and S-0.5's evidence for why fewer is not enough:
rollback alone restored state on nine cycles out of ten."""


class Outcome(StrEnum):
    """What a check concluded. Three values, and the third is not the second."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    """Its inputs were not supplied, so the requirement was never tested.

    Never merged into `PASSED`. An adapter checked without a database has not
    been shown to reset reliably; it has been shown nothing about resetting.
    """


@dataclass(frozen=True)
class Result:
    """One check, what it demanded, and what happened."""

    check: str
    requirement: str
    """What the interface asks for, in a sentence, so a failure is actionable
    without reading this module."""

    outcome: Outcome
    detail: str = ""

    def describe(self) -> str:
        """One line, the detail, and **the requirement when it failed**.

        A failure is read by somebody who did not write this suite, and *what was
        required* is the half they cannot reconstruct from the detail.
        """
        mark = {Outcome.PASSED: "ok  ", Outcome.FAILED: "FAIL", Outcome.SKIPPED: "skip"}
        lines = [f"  {mark[self.outcome]}  {self.check}"]
        if self.outcome is Outcome.FAILED:
            lines.append(f"        required: {self.requirement}")
        if self.detail:
            lines.append(f"        {self.detail}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Report:
    """Everything one conformance run established, and everything it did not."""

    adapter: str
    results: tuple[Result, ...]

    def _of(self, outcome: Outcome) -> tuple[Result, ...]:
        return tuple(result for result in self.results if result.outcome is outcome)

    @property
    def failures(self) -> tuple[Result, ...]:
        return self._of(Outcome.FAILED)

    @property
    def skips(self) -> tuple[Result, ...]:
        return self._of(Outcome.SKIPPED)

    @property
    def conforms(self) -> bool:
        """No check failed. **Not the same as every requirement being met.**"""
        return not self.failures

    @property
    def attested(self) -> bool:
        """Nothing failed and nothing was skipped, so every requirement was tested.

        The property to gate on when the question is whether this adapter has
        actually been checked. `conforms` answers the narrower question, and an
        adapter run with no inputs conforms trivially.
        """
        return self.conforms and not self.skips

    def describe(self) -> str:
        counts = (
            f"{len(self._of(Outcome.PASSED))} passed, "
            f"{len(self.failures)} failed, {len(self.skips)} skipped"
        )
        lines = [f"Adapter conformance — {self.adapter}: {counts}", ""]
        lines.extend(result.describe() for result in self.results)
        if self.skips:
            lines.extend(
                [
                    "",
                    "A skipped check is not a passed one. This run did not test: "
                    + ", ".join(result.check for result in self.skips)
                    + ". Supply the inputs those checks name and run it again.",
                ]
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class Inputs:
    """The adapter, and whatever the caller can give the suite to work with.

    Everything past `subject` is optional and each absence skips the checks that
    needed it. That is the whole reason `SKIPPED` exists as a third outcome:
    the alternative designs are refusing to run without a live environment, which
    makes the suite useless during development, and quietly passing, which makes
    it worse than useless.
    """

    adapter: FrameworkAdapter
    subject: Subject

    session: CandidateSession | None = None
    """A candidate session over the subject's checkout.

    Needed by `run_tests`, `read_source` and `apply_patch`. It must be a real
    one: the protected-path check is only meaningful against the session that
    owns the filter.
    """

    entry_point: str | None = None
    """Something `run_workload` can drive. Without it the measurement checks —
    the ones that catch a fabricated `Drive` — do not run."""

    repeats: int = 3
    scale: int = 10
    created: Mapping[str, int] = field(default_factory=dict)

    database: VerifiedDatabase | None = None
    """A test database, for the reset-reliability check."""

    mutate: Callable[[], object] | None = None
    """A workload that changes the database, driven once per reset cycle.

    S-2.7 verifies a reset by making a mess and checking it goes away; a reset
    verified against a workload that writes nothing verifies nothing.
    """

    events: Callable[[], object] | None = None
    """A workload that raises counted events, for the overhead check."""

    event_count: int = 0
    """How many events `events` raises. Supplied rather than counted, because the
    count taken *through the hook* is the number under test."""

    timeout: float = 300.0


# --------------------------------------------------------------- the checks


def _passed(check: str, requirement: str, detail: str = "") -> Result:
    return Result(check=check, requirement=requirement, outcome=Outcome.PASSED, detail=detail)


def _failed(check: str, requirement: str, detail: str) -> Result:
    return Result(check=check, requirement=requirement, outcome=Outcome.FAILED, detail=detail)


def _skipped(check: str, requirement: str, detail: str) -> Result:
    return Result(check=check, requirement=requirement, outcome=Outcome.SKIPPED, detail=detail)


def check_identity(inputs: Inputs) -> Result:
    check = "identity"
    requirement = "declares the framework it is for and the ORM that framework uses"
    adapter = inputs.adapter
    orm = adapter.declarations.orm
    return _passed(check, requirement, f"{adapter.framework.value} on {orm.value}")


def check_hooks_declared(inputs: Inputs) -> Result:
    """The names, not the mechanisms. Registration never calls a hook.

    `installed` validates through `register_counter`, which refuses a name
    outside the catalogue, a counter that is framework-free, and a name that is a
    *reading* of another counter's hook. None of that touches the framework, so
    this runs on a machine with no database and no subject standing.
    """
    check = "hooks are catalogue counters"
    requirement = "every declared hook is a catalogue counter an adapter may supply"
    declarations = inputs.adapter.declarations
    if not declarations.hooks:
        return _failed(check, requirement, "no hooks are declared, so nothing can be counted")
    try:
        with installed(declarations):
            pass
    except Exception as error:  # noqa: BLE001 - the check's whole job is to report it
        return _failed(check, requirement, f"{type(error).__name__}: {error}")
    return _passed(check, requirement, f"declared: {', '.join(sorted(declarations.hooks))}")


def check_query_counter(inputs: Inputs) -> Result:
    check = "a query counter is declared"
    requirement = f"declares {DB_QUERY!r}, without which no database finding can be measured"
    if DB_QUERY in inputs.adapter.declarations.hooks:
        return _passed(check, requirement)
    return _failed(check, requirement, f"declares {sorted(inputs.adapter.declarations.hooks)}")


def check_capabilities(inputs: Inputs) -> Result:
    """S-14.1 deferred this here, because a Protocol cannot constrain a return."""
    check = "capabilities are the adapter's own"
    requirement = "claims only capabilities from ADAPTER_CAPABILITIES"
    claimed = frozenset(inputs.adapter.capabilities())
    overclaimed = sorted(capability.value for capability in claimed - ADAPTER_CAPABILITIES)
    if overclaimed:
        return _failed(
            check,
            requirement,
            f"claims {overclaimed}, which the harness supplies. A primitive would be "
            "offered on the strength of an implementation this adapter has never seen",
        )
    return _passed(check, requirement, f"claims {sorted(c.value for c in claimed)}")


def check_protected_paths(inputs: Inputs) -> Result:
    check = "protected paths only widen"
    requirement = "the default protected patterns all survive the adapter's policy"
    policy = inputs.adapter.declarations.patch_policy()
    missing = [rule for rule in DEFAULT_PROTECTED_PATTERNS if rule not in policy.protected]
    if missing:
        return _failed(
            check,
            requirement,
            f"dropped {missing}. A patch editing the tests that decide whether it worked "
            "would apply cleanly",
        )
    return _passed(check, requirement, f"{len(policy.protected)} rules, defaults intact")


def check_internal_frames(inputs: Inputs) -> Result:
    """Built from the adapter's *own* first fragment, so it works for any framework.

    **What this can and cannot establish.** It catches an empty declaration —
    a real and likely omission, and one that leaves every localization stopping
    at the framework's deepest frame — and it catches a `Declarations` subclass
    whose `localizer` does not pass the list on. It **cannot** tell whether the
    fragments are the *right* ones for the framework: the stack is synthesized
    from the adapter's own first fragment, because a harness that knew what a
    Flask stack looks like would be a harness with a framework in it. Checking
    the fragments against real stacks is the implementer's own test to write, and
    `docs/09-adapters.md` says so.
    """
    check = "framework frames are declared and reach the localizer"
    requirement = "declares path fragments for its framework, and they reach `localize`"
    declarations = inputs.adapter.declarations
    if not declarations.internal_frames:
        return _failed(
            check,
            requirement,
            "no internal frames are declared, so every localization stops at the "
            "framework's deepest frame — a line nobody investigating their own project "
            "can change",
        )

    fragment = declarations.internal_frames[0].replace("\\", "/").strip("/")
    subject_file = str(Path(inputs.subject.root) / "subject_module.py")
    stack = (
        Frame(filename=f"/env/{fragment}/internals.py", lineno=1, function="framework"),
        Frame(filename=subject_file, lineno=2, function="subject"),
    )
    site = declarations.localizer().localize([stack]).causal_site
    if site is None or site.filename != subject_file:
        found = site.filename if site else "nothing"
        return _failed(check, requirement, f"localized to {found} rather than to {subject_file}")
    return _passed(check, requirement, f"{len(declarations.internal_frames)} fragments declared")


def check_discovery(inputs: Inputs) -> Result:
    """Enumeration has to be repeatable, because a ranking feeds a cached prompt.

    ADR 002: a tool list that changes between runs invalidates every cached token
    after it. A discovery whose order depends on filesystem iteration is a
    discovery that costs money at random.
    """
    check = "workload discovery is stated and repeatable"
    requirement = "returns a ranked enumeration whose order does not change between calls"
    try:
        first = inputs.adapter.discover_workloads(inputs.subject, timeout=inputs.timeout)
        second = inputs.adapter.discover_workloads(inputs.subject, timeout=inputs.timeout)
    except Exception as error:  # noqa: BLE001 - reported rather than raised, like every check
        return _failed(check, requirement, f"{type(error).__name__}: {error}")

    if [entry.candidate.name for entry in first.scored] != [
        entry.candidate.name for entry in second.scored
    ]:
        return _failed(check, requirement, "two calls ranked the same repository differently")
    if not first.resolution.available and not first.resolution.error:
        return _failed(
            check,
            requirement,
            "the route table was not resolved and no reason is given, so a caller cannot "
            "tell an incomplete enumeration from a complete one",
        )
    return _passed(
        check,
        requirement,
        f"{len(first.scored)} candidate(s); {first.resolution.describe().splitlines()[0]}",
    )


def check_seed_refusal(inputs: Inputs) -> Result:
    """An adapter that cannot seed must say so in its own words.

    Only meaningful for an adapter that does not claim `FIXTURE_SEEDING` — one
    that does has to be checked by seeding, which is the operator's to drive
    against a standing subject.
    """
    check = "seeding refuses cleanly"
    requirement = "an adapter that cannot seed raises a typed error naming what is missing"
    if Capability.FIXTURE_SEEDING in inputs.adapter.capabilities():
        return _skipped(
            check,
            requirement,
            "this adapter claims it can seed, so the refusal path does not apply; drive "
            "`seed` against a standing subject instead",
        )
    try:
        inputs.adapter.seed(inputs.subject, scale=inputs.scale, timeout=inputs.timeout)
    except (ValueError, LookupError) as error:
        return _passed(check, requirement, f"{type(error).__name__}: {str(error)[:120]}")
    except Exception as error:  # noqa: BLE001 - the point is which kind of error it was
        return _failed(
            check,
            requirement,
            f"raised {type(error).__name__}, which reads as a defect in the harness rather "
            f"than as a missing fixture mechanism: {error}",
        )
    return _failed(check, requirement, "seeded without a mechanism, so the rows came from where?")


def check_measurement(inputs: Inputs) -> Result:
    """The anti-fabrication check. See this module's docstring.

    Nothing here knows what the right numbers are. What it knows is what the
    caller asked for, and every relationship below is one the caller can verify
    without understanding the framework.
    """
    check = "the measurement is self-consistent"
    requirement = (
        "returns as many samples as repeats requested, a median that is the median of "
        "them, and the scale and row counts it was given"
    )
    if inputs.entry_point is None:
        return _skipped(check, requirement, "no entry_point was supplied to drive")

    try:
        measured = inputs.adapter.run_workload(
            inputs.subject,
            entry_point=inputs.entry_point,
            scale=inputs.scale,
            created=inputs.created,
            repeats=inputs.repeats,
            timeout=inputs.timeout,
        )
    except Exception as error:  # noqa: BLE001 - reported rather than raised
        return _failed(check, requirement, f"{type(error).__name__}: {error}")

    for complaint in _measurement_complaints(measured, inputs):
        return _failed(check, requirement, complaint)
    return _passed(check, requirement, measured.describe())


def _measurement_complaints(measured: Drive, inputs: Inputs) -> Iterator[str]:
    if len(measured.samples) != inputs.repeats:
        yield (
            f"{inputs.repeats} repeat(s) were requested and {len(measured.samples)} sample(s) "
            "came back. A driver that runs once and reports several is reporting one "
            "measurement several times"
        )
    elif measured.seconds != statistics.median(measured.samples):
        yield (
            f"the reported median {measured.seconds:.6f}s is not the median of the samples "
            f"({statistics.median(measured.samples):.6f}s), so one of the two was not measured"
        )
    if measured.scale != inputs.scale:
        yield f"asked for scale {inputs.scale} and the result says {measured.scale}"
    if dict(measured.created) != dict(inputs.created):
        yield "the row counts handed in did not come back unchanged"
    if measured.warmup_seconds <= 0:
        yield (
            "no warm-up was recorded. A first request charged to the small scale point is "
            "how a flat workload comes to look like a growing one"
        )


def check_suite_command(inputs: Inputs) -> Result:
    check = "the test suite can be run"
    requirement = "runs the subject's own suite in the session it was given"
    if inputs.session is None:
        return _skipped(check, requirement, "no session was supplied")
    try:
        result = inputs.adapter.run_tests(inputs.session, timeout=inputs.timeout)
    except Exception as error:  # noqa: BLE001 - reported rather than raised
        return _failed(check, requirement, f"{type(error).__name__}: {error}")
    if not result.command:
        return _failed(check, requirement, "ran an empty command")
    return _passed(check, requirement, " ".join(result.command))


def check_source_reading(inputs: Inputs) -> Result:
    check = "source is readable and worktree-relative"
    requirement = "returns paths relative to the worktree, each of which exists"
    if inputs.session is None:
        return _skipped(check, requirement, "no session was supplied")
    try:
        sources = inputs.adapter.read_source(inputs.session)
    except Exception as error:  # noqa: BLE001 - reported rather than raised
        return _failed(check, requirement, f"{type(error).__name__}: {error}")

    root = inputs.session.worktree.path
    absolute = [name for name in sources if Path(name).is_absolute()]
    if absolute:
        return _failed(
            check,
            requirement,
            f"{absolute[:3]} are absolute; a patch names paths the way the diff does",
        )
    missing = [name for name in sources if not (root / name).exists()]
    if missing:
        return _failed(check, requirement, f"{missing[:3]} do not exist under {root}")
    return _passed(check, requirement, f"{len(sources)} file(s)")


def check_patch_refusal(inputs: Inputs) -> Result:
    """The safety check, and the reason a real session is required.

    An adapter that writes the file itself rather than handing the diff to the
    session bypasses the protected-path filter entirely, and every other check in
    this suite passes for it.
    """
    check = "a protected path is refused"
    requirement = "a diff touching the test suite is rejected, not applied"
    if inputs.session is None:
        return _skipped(check, requirement, "no session was supplied")

    diff = (
        f"--- a/{PROTECTED_PROBE}\n+++ b/{PROTECTED_PROBE}\n"
        "@@ -1 +1 @@\n-def test_it(): assert True\n+def test_it(): pass\n"
    )
    try:
        written = inputs.adapter.apply_patch(inputs.session, diff)
    except ProtectedPathError as refusal:
        return _passed(check, requirement, str(refusal)[:160])
    except Exception as error:  # noqa: BLE001 - any other error is not the refusal asked for
        return _failed(
            check,
            requirement,
            f"raised {type(error).__name__} rather than refusing the path: {error}",
        )
    return _failed(
        check,
        requirement,
        f"applied a patch to {sorted(written)}. The protected-path filter was not consulted, "
        "which means this adapter has a route from a diff to a file that nothing checks",
    )


def check_reset_reliability(inputs: Inputs) -> Result:
    """S-2.7's `verify`, driven over whatever the adapter offers."""
    check = "a reset restores state"
    requirement = f"one offered mechanism returns identical row counts over {DEFAULT_CYCLES} cycles"
    if inputs.database is None or inputs.mutate is None:
        return _skipped(check, requirement, "no database and mutating workload were supplied")

    mechanisms = inputs.adapter.reset_state(inputs.subject)
    if not mechanisms:
        return _failed(
            check,
            requirement,
            "no reset mechanism is offered, so nothing can be measured twice from the same "
            "starting state",
        )
    try:
        verified = choose_reset(mechanisms, inputs.database, inputs.mutate, cycles=DEFAULT_CYCLES)
    except NoReliableResetError as error:
        return _failed(check, requirement, str(error)[:400])
    return _passed(check, requirement, f"{verified.mechanism.strategy.value} verified")


def check_hook_overhead(inputs: Inputs) -> Result:
    """Per event, against a stated denominator. See `REFERENCE_OPERATION_SECONDS`."""
    check = "the query counter is cheap enough"
    requirement = (
        f"costs under {OVERHEAD_BUDGET:.0%} of a "
        f"{REFERENCE_OPERATION_SECONDS * 1e6:.0f}us operation, per event"
    )
    if inputs.events is None or inputs.event_count < 1:
        return _skipped(check, requirement, "no event-raising workload and count were supplied")

    bare = _median_seconds(inputs.events)
    with installed(inputs.adapter.declarations), count(DB_QUERY):
        instrumented = _median_seconds(inputs.events)

    per_event = (instrumented - bare) / inputs.event_count
    share = per_event / REFERENCE_OPERATION_SECONDS
    detail = (
        f"{per_event * 1e6:.3f}us per event, {share:.2%} of the reference operation "
        f"({inputs.event_count} events)"
    )
    if share >= OVERHEAD_BUDGET:
        return _failed(check, requirement, detail)
    return _passed(check, requirement, detail)


def _median_seconds(work: Callable[[], object], rounds: int = 5) -> float:
    """The median of `rounds` timings. Odd by default, so it is a measured value."""
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        work()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


CHECKS: tuple[Callable[[Inputs], Result], ...] = (
    check_identity,
    check_hooks_declared,
    check_query_counter,
    check_capabilities,
    check_protected_paths,
    check_internal_frames,
    check_discovery,
    check_seed_refusal,
    check_measurement,
    check_suite_command,
    check_source_reading,
    check_patch_refusal,
    check_reset_reliability,
    check_hook_overhead,
)
"""Every check, in the order a report renders them.

Declaration checks first, because they need nothing and a failure among them
explains most failures below. Order is fixed rather than derived from a
dictionary, so two runs of the same adapter produce comparable reports.
"""


def run_conformance(inputs: Inputs) -> Report:
    """Run every check and collect what each concluded.

    **No check raises.** A suite that stops at the first failure tells an
    implementer about one problem per run, and the whole value of a conformance
    report is that it is a list. Each check catches broadly on purpose and turns
    the exception into a `FAILED` with the type and message.
    """
    return Report(
        adapter=type(inputs.adapter).__name__,
        results=tuple(check(inputs) for check in CHECKS),
    )
