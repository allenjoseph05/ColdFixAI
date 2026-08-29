"""Turning a `Workload` artifact back into something runnable. **`Resources.bind`.**

S-17.10, and the story the whole measurement-boundary thread was for. `Workload`
survives a checkpoint and `BoundWorkload` cannot, because the second one holds the
callables that seed, invoke and reset the subject — so screening cannot read its
input straight out of the channel it was written to, and something has to rebuild
it. This is that something.

**Every number comes from the subject and the type is what says so.** S-17.5
measured what happens if the harness times an out-of-process subject instead:
1266 ms recorded for a 9.6 ms endpoint, and the same workload at three scales
fitting `LINEAR` inside the subject and `CONSTANT` outside it — a wrong growth
class on a metric this system publishes exclusions about. So `invoke` drives the
subject and remembers nothing but what the subject reported, and `extra_counters`
is a `Reported`, which is S-17.6's way of saying so without anybody being able to
forget.

**The cell between them is the dangerous part.** `scale_volume` runs reset, seed,
invoke, and *then* reads the counters, once per scale point. A cell that survived
into the next point would report the previous scale's numbers, and a growth fit
over one measurement repeated is `CONSTANT` — S-17.5's failure again, by a
different route. So the cell is cleared before every drive and reading it twice,
or reading it before a drive, is refused rather than stale.

**`db.query` is reported, not hooked.** S-17.10 measured that a hook installed here
counts this process — zero, against a subject running somewhere else — and files
that zero under the name the subject's real count belongs to. `drive` already
returns the subject's own count, so it travels with the rest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from coldfix.explorer.surface import Surface
from coldfix.explorer.synthesis import synthesize
from coldfix.explorer.work import Drive, drive
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS, Reported
from coldfix.primitives.scaling import BASELINE_SCALE
from coldfix.sandbox.verification import VerifiedReset
from coldfix.screening.workload import RESPONSE_BYTES, BoundWorkload, Workload


class BindingError(Exception):
    """A workload artifact could not be turned back into a runnable binding."""


@dataclass
class _Latest:
    """The one drive a scale point measured, and nothing older.

    Mutable and deliberately awkward to read: `taken()` clears as it returns, so a
    second read raises instead of handing back a number the current invocation did
    not produce. That is the whole safety property of this module — a stale read
    is not a wrong value in one metric, it is a growth curve fitted over the same
    measurement three times.
    """

    workload: str
    drive: Drive | None = None

    def record(self, taken: Drive) -> None:
        self.drive = taken

    def taken(self) -> Drive:
        if self.drive is None:
            message = (
                f"{self.workload}'s counters were read without a drive to read them from. "
                "Under the subject vantage the harness measures nothing itself, so this would "
                "be an experiment reporting numbers nobody took"
            )
            raise BindingError(message)
        taken, self.drive = self.drive, None
        return taken


def bind_workload(  # noqa: PLR0913 - the artifact, the checkout, its interpreter,
    # where commands run, the reset proof and how many samples to take are six
    # facts from four owners, and none is derivable from the others.
    descriptor: Workload,
    *,
    root: Path,
    python: Sequence[str],
    surface: Surface,
    reset: VerifiedReset,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    repeats: int = 3,
) -> BoundWorkload:
    """One artifact, rebound to the subject it was emitted from.

    `entry_point` is the request path, `fixture.entity` is what `scale(n)` seeds n
    of, and `fixture.distribution` is the shape — all three off the artifact,
    because a binding that chose any of them would be measuring a different
    workload from the one the screen names.
    """
    latest = _Latest(descriptor.id)
    created: dict[str, int] = {}
    at_scale = {"n": 0}
    drives = {"n": 0}

    # Synthesis rather than the repository's own factory: the artifact records
    # `fixture.source`, but re-deriving a factory here would need the module it is
    # importable from, which `FixtureRecipe` does not carry. A binding that guessed
    # would seed a different table than the one the screen names.
    def scale(n: int) -> None:
        # **N=0 is the baseline and it means an empty subject, not a seeding plan
        # for nothing.** `scale_volume` measures a baseline at zero to establish
        # the framework's own fixed cost, and subtracts it from every point — and
        # it calls `seed` inside `reset.mechanism.cycle()`, so the subject has
        # already been emptied by the time this runs. `synthesize` refuses a
        # zero-row plan on its own correct terms (*a plan for 0 row(s) seeds
        # nothing and would report success for it*), so asking it for one is what
        # the Epic 17 composition check found: two modules each right, and no
        # screen possible between them.
        created.clear()
        at_scale["n"] = n
        if n == BASELINE_SCALE:
            return

        synthesized = synthesize(
            root,
            python=python,
            target=descriptor.fixture.entity,
            count=n,
            per_parent=descriptor.fixture.per_parent,
            distribution=descriptor.fixture.distribution,
            surface=surface,
        )
        created.update(synthesized.created)

    def invoke() -> object:
        taken = drive(
            root,
            python=python,
            path=descriptor.entry_point,
            scale=at_scale["n"],
            created=dict(created),
            headers=headers,
            cookies=cookies,
            repeats=repeats,
            surface=surface,
        )
        latest.record(taken)
        drives["n"] += 1
        # **Deliberately not the response body.** Under the subject vantage
        # `measure_once` does not drain what `invoke` returns — there is no
        # `materialized` — so a caller reading this would be reading something no
        # measurement is taken from. The numbers are on the `Reported`.
        return None

    def reported() -> Mapping[str, float]:
        taken = latest.taken()
        return {
            SECONDS: taken.seconds,
            DB_QUERY: float(taken.queries),
            RESPONSE_BYTES: float(taken.response_bytes),
        }

    return BoundWorkload(
        descriptor,
        invoke=invoke,
        scale=scale,
        reset=reset,
        # **S-3.2's cache control, and the claim rests on a `Surface` invariant.**
        # Every `Surface.run` starts a new process — a subprocess on the host, a
        # container the sandbox destroys before returning — so the interpreter
        # that served one scale point cannot be the one that serves the next, and
        # nothing it cached in memory survives. Counting drives is therefore
        # reporting that fact rather than asserting freshness nobody checked; the
        # invariant is measured in `tests/explorer/test_surface.py`.
        process_identity=lambda: (surface.root, drives["n"]),
        extra_counters=Reported(reported),
    )


def binder_for(  # noqa: PLR0913 - the same six facts as `bind_workload`, minus the
    # artifact, which is what the returned callable takes.
    root: Path,
    *,
    python: Sequence[str],
    surface: Surface,
    reset: VerifiedReset,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    repeats: int = 3,
) -> object:
    """Bind many. **The producer for `Resources.bind`.**

    Returns a callable taking a sequence of artifacts, which is the `Binder`
    protocol. Typed `object` rather than `Binder` for `grounder_for`'s reason: the
    protocol lives in `orchestrator.adapters` and importing it here would point
    screening at the layer above it, so a test asserts the shape structurally.
    """

    def bind(workloads: Sequence[Workload]) -> Sequence[BoundWorkload]:
        return [
            bind_workload(
                descriptor,
                root=root,
                python=python,
                surface=surface,
                reset=reset,
                headers=headers,
                cookies=cookies,
                repeats=repeats,
            )
            for descriptor in workloads
        ]

    return bind
