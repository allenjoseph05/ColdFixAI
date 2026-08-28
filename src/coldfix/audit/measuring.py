"""Before and after, for the two attacks that reason over numbers. **`Resources.measure`.**

S-17.14, and the last of the six. `cheating.detect` and `trades.audit_trades` both
measure nothing themselves — `CLAUDE.md` puts the measuring in the harness — so
both are handed results, and `Measurements` is the shape the orchestrator has to
fill. Nothing filled it.

**The two revisions are measured on two sessions and that is the whole safety
property.** `Revision.ORIGINAL` runs on the diagnostic session, `Revision.PATCHED`
on the candidate. A `Measure` that read one session twice returns identical
numbers for both, `_read`'s tag check passes because the tags are whatever the
harness put on them, and the audit reports every class absent — a patch cleared by
a measurement that never distinguished it from the original.

**`Reading.first` is the cold pass and it is a measurement, not a duration.**
S-17.14 found the warm-up running outside `CaptureQueriesContext`, so a `Drive`
carried `warmup_seconds` and no count for the pass that paid for the imports. That
made `_cached_state` — which compares the patch's warm-up excess on whichever
metric the patch claims to reduce — unable to run on a count, and a count is what
an N+1 patch reduces. The driver now measures every pass, warm-up included.

**The envelope is the subject's, not the harness's.** `primitives.envelope` reads
`RUSAGE_SELF`, this interpreter's allocated blocks and this process's thread count.
Wrapped around a containerised drive it reports what the harness did while it
waited, and `audit_trades` would compare two samples of the same idle interpreter
and find every trade absent. So the subject samples itself either side of its own
drive and reports the levels back, under `primitives.envelope`'s own metric names
— a second spelling would leave every metric silently unwatched.

**What is supplied rather than measured**: `metrics` (only the adapter knows what
its counters are called), `shape` and `alternatives` (the fixture shapes to sweep),
and `claim` (what the patch promised, which comes from the repair). Measuring any
of them here would be this module deciding what the patch was for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from coldfix.audit.cheating import Metrics, Reading, Revision
from coldfix.audit.patchcompose import Measurements
from coldfix.explorer.surface import SessionSurface, Surface
from coldfix.explorer.synthesis import synthesize
from coldfix.explorer.work import Drive, drive
from coldfix.primitives.envelope import ENVELOPE, Availability, EnvelopeSample
from coldfix.primitives.scaling import Distribution
from coldfix.repair.falsification import CostClaim
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession


class MeasuringError(Exception):
    """The two revisions could not be measured."""


def sample_of(levels: Mapping[str, float]) -> EnvelopeSample:
    """One subject-side reading, as the envelope the trade audit compares.

    Every metric `ENVELOPE` names is present — as a number where the subject could
    report it and `None` where it could not, with the reason recorded. An absent
    key would read as *not watched*; a `None` with an `Availability` beside it
    reads as *looked for, and this platform cannot say*, which is the distinction
    `trades` needs to report a guard it could not evaluate rather than one that
    passed.
    """
    sample = EnvelopeSample()
    for name in ENVELOPE:
        value = levels.get(name)
        sample.metrics[name] = value
        if value is None:
            sample.unavailable[name] = (
                Availability.NEEDS_PROC
                if name in {"open_file_descriptors", "process_count"}
                else Availability.NEEDS_RUSAGE
            )
    return sample


def reading_of(taken: Drive, *, revision: Revision, shape: Distribution) -> Reading:
    """One drive, as the reading the cheat detector compares.

    Raises:
        MeasuringError: the drive reported no cold pass. Under the old driver that
            was every drive, and a `Reading` built from the repeats alone would
            answer the cached-state question with the warm-up folded into the
            thing it is supposed to be measured against.
    """
    if not taken.warm_pass:
        message = (
            f"{revision.name} at {shape.value} reported no cold pass, so there is nothing to "
            "compare the repeats against. `Reading.first` is the pass that paid for whatever "
            "the process had not yet warmed, and a reading without one cannot answer the "
            "cached-state question at all"
        )
        raise MeasuringError(message)
    return Reading(
        revision=revision,
        shape=shape,
        first=dict(taken.warm_pass),
        repeated=tuple(dict(measured) for measured in taken.passes),
    )


def measurer_for(  # noqa: PLR0913 - the two sessions, the route, what to seed, the
    # interpreter and how many repeats are six facts from four owners, and the two
    # sessions are the pair this module exists to keep apart.
    *,
    diagnostic: DiagnosticSession,
    python: Sequence[str],
    path: str,
    entity: str,
    metrics: Metrics,
    claim: CostClaim,
    shape: Distribution = Distribution.UNIFORM,
    alternatives: Sequence[Distribution] = (),
    scale: int = 40,
    repeats: int = 3,
) -> object:
    """Measure a patch before and after. **The producer for `Resources.measure`.**

    `diagnostic` is bound here and the candidate arrives per patch, which is the
    `Measurer` protocol — and it is the right split: the original revision is the
    same for every patch of one finding, while the candidate is what changes.

    Returns a callable typed `object` for `grounder_for`'s reason: `Measurer` lives
    in `orchestrator.adapters` and importing it here would point the audit at the
    layer above it. A test asserts the produced callable satisfies the protocol.
    """
    original = SessionSurface(diagnostic)

    def read(surface: Surface, revision: Revision, at: Distribution) -> Drive:
        synthesized = synthesize(
            surface.root,
            python=python,
            target=entity,
            count=scale,
            distribution=at,
            surface=surface,
        )
        return drive(
            surface.root,
            python=python,
            path=path,
            scale=scale,
            created=dict(synthesized.created),
            repeats=repeats,
            surface=surface,
        )

    def measure(patch: object, *, candidate: CandidateSession) -> Measurements:
        del patch  # the diff is already applied to `candidate`; this measures it
        patched = SessionSurface(candidate)

        def at(revision: Revision, requested: Distribution) -> Reading:
            # **The revision decides the session, and nothing else does.** A
            # `Measure` reading one session twice reports identical numbers for
            # both revisions, and `_read`'s tag check cannot catch it because the
            # tags are whatever this function puts on them.
            surface = original if revision is Revision.ORIGINAL else patched
            return reading_of(
                read(surface, revision, requested), revision=revision, shape=requested
            )

        before = read(original, Revision.ORIGINAL, shape)
        after = read(patched, Revision.PATCHED, shape)

        return Measurements(
            measure=at,
            metrics=metrics,
            shape=shape,
            alternatives=tuple(alternatives),
            envelope_before=sample_of(before.envelope_before),
            envelope_after=sample_of(after.envelope_after),
            # The workload's own counters, which is what `domain` means here: the
            # envelope is fixed and these are whatever the adapter counts.
            domain_before=_domain(before),
            domain_after=_domain(after),
            claim=claim,
        )

    return measure


def _domain(taken: Drive) -> dict[str, float]:
    """What the workload itself cost, from the passes rather than the aggregate.

    The last pass, matching what `Drive.queries` and `Drive.response_bytes` already
    report — stated rather than left implicit, because a trade audit comparing a
    median against a last-pass count would be comparing two different things.
    """
    if not taken.passes:
        return {}
    return dict(taken.passes[-1])
