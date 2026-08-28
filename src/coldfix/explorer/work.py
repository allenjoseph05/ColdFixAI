"""Whether a workload does real work, decided by the harness and by nobody else.

Epic 7, S-7.8, and a **safety** story. The note on it is the whole reason it
exists: *the agent is incentivized to claim success because success completes its
task.* An Explorer that has spent fifty steps standing a repository up, getting
past its login and seeding it has every reason to report that the endpoint it
finally reached does something — and no reason at all to notice that it returns
the same four hundred bytes whether the database holds ten rows or ten thousand.

**The verdict already exists and is not re-implemented here.** S-4.1 put
`work_verified` on the workload artifact as a property with no field behind it,
and `08-audit.md` F6 is the test it applies. What was missing is the half that
produces the numbers it reads: something that seeds a subject at two volumes,
drives the candidate at each, and measures. That is this module.

**The agent supplies how to reach the subject and never a number.** `CLAUDE.md`
is explicit — *do not let an agent report a measurement; agents reason about
measurements the harness took* — so the parameters here are a path, an
interpreter and a fixture plan. There is no argument through which a query count,
a byte count or a duration could arrive, which is the same construction S-1.6
used when `compare()` was made to accept callables only.

**`accept` takes one argument for the same reason.** AC 4 asks that a workload
failing verification be rejected *regardless of what the agent claims*, and the
way to guarantee that is to give a claim nowhere to enter: the gate reads the
harness's own measurements and has no parameter a claim could occupy.

**The three metrics are measured in the subject's interpreter, not inferred from
outside.** Wall time and response bytes could be taken from a socket, but the
query count cannot — nothing outside the process knows how many statements a
request issued. So one program drives the request under
`CaptureQueriesContext` and reports all three together, which also keeps them
describing the same invocation rather than three separate ones.

**F6's first condition is the corrected one, and this module does not restate the
uncorrected version.** ADR 051 established that *queries rose* rejects every
correctly batched endpoint — two queries at ten rows and two at a hundred is what
a prefetched list view does, and it is the shape this tool exists to produce — so
the condition is *queries did not fall*. This story's acceptance criterion still
carries the audit's original wording; the correction is older than the criterion
and the backlog records why.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from coldfix.bench.execute import ExecutionError
from coldfix.explorer.entrypoints import settings_module
from coldfix.explorer.surface import HostSurface, Surface
from coldfix.explorer.synthesis import SYNTHESIS_TIMEOUT_SECONDS, synthesize
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import (
    MINIMUM_SCALE_RATIO,
    RESPONSE_BYTES,
    EnvironmentAnchor,
    FixtureRecipe,
    Observation,
    Workload,
)

DEFAULT_SCALES: tuple[int, int] = (10, 100)
"""F6's own numbers. Ten and a hundred are what the thresholds were written
against, and `08-audit.md` is explicit that applying them across a narrower
spread demands a doubled payload for a small increase in data."""

DEFAULT_REPEATS = 5
"""Invocations per scale point, before the warm-up.

The median of five rather than one reading, because the wall-time condition is a
ratio and a single slow scheduling slice on the small point makes a real workload
look flat. Odd on purpose, so the median is a measured value rather than the mean
of two."""

DRIVE_TIMEOUT_SECONDS = 600.0
"""Seeding a hundred rows and then driving the endpoint six times, against
whatever database the subject configured."""

_MARKER = "<<<COLDFIX-WORK>>>"


class Seeder(Protocol):
    """How the subject is filled with data before a scale point is driven.

    **Added by the Epic 7 composition check.** S-7.5 exists to *use a
    repository's own factories in preference to synthesis*, and until the epic was
    run end to end the only code that seeded at scale called `synthesize`
    unconditionally — so that acceptance criterion was satisfied inside its own
    module and unreachable from everywhere else.

    A seam rather than a decision: the default is still synthesis, because it
    needs nothing but a schema, and a caller that has located a factory passes
    one. S-7.5 is deliberate that the *module path* of a factory is the caller's
    to supply — a `src/` layout means the checkout root is not the import root —
    so deriving it here would be the guess that story declined to make.
    """

    def __call__(
        self, *, root: Path, python: Sequence[str], scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]: ...


class WorkVerificationError(Exception):
    """The workload could not be driven, or was driven and does no real work."""


@dataclass(frozen=True)
class Drive:
    """What one scale point measured, and how.

    Every field is the harness's own reading. `samples` is kept beside the median
    because a ratio taken between two medians is only as honest as the spread
    behind them, and a reader deciding whether to believe a 1.6x needs to see
    whether the samples overlapped.
    """

    scale: int
    queries: int
    response_bytes: int
    seconds: float
    samples: tuple[float, ...]
    warmup_seconds: float
    """What the first, discarded invocation cost.

    Kept rather than thrown away for two reasons. It is the evidence that a
    warm-up happened at all — a run whose first request is charged to the small
    scale point is how a flat workload comes to look like a growing one, and a
    field that is simply absent when the warm-up is removed makes that visible.
    And where it dwarfs the median it is worth reading: a subject whose first
    request costs fifty times its second is a subject whose imports, not whose
    data, dominated the first number anyone measured."""

    status: int
    created: Mapping[str, int]

    passes: tuple[Mapping[str, float], ...] = ()
    """What **each** repeat measured, not just the aggregate. S-17.14.

    `seconds`, `db.query` and `response_bytes` per pass, in order. The audit needs
    them because `Reading` distinguishes the cold pass from the ones after it *in
    the same process*, and two fresh processes cannot see state carried across
    runs — neither would be the second run of anything. Empty on a `Drive` an
    adapter built from aggregates alone."""

    warm_pass: Mapping[str, float] = field(default_factory=dict)
    """What the discarded first request measured, counters included.

    It was `warmup_seconds` alone, which left `_cached_state` unable to run on a
    count — the metric an N+1 patch claims to reduce."""

    envelope_before: Mapping[str, float] = field(default_factory=dict)
    envelope_after: Mapping[str, float] = field(default_factory=dict)
    """The subject's own resource levels either side of the drive.

    Taken **inside the subject**, because `primitives.envelope` reads
    `RUSAGE_SELF`, this interpreter's allocated blocks and this process's thread
    count — wrapped around a containerised drive it measures the harness waiting."""

    def observation(self) -> Observation:
        """The artifact form S-4.1's verdict reads."""
        return Observation(
            scale=self.scale,
            metrics={
                DB_QUERY: float(self.queries),
                RESPONSE_BYTES: float(self.response_bytes),
                SECONDS: self.seconds,
            },
        )

    def describe(self) -> str:
        spread = f"{min(self.samples):.4f} to {max(self.samples):.4f}" if self.samples else "?"
        return (
            f"n={self.scale}: {self.queries} quer(ies), {self.response_bytes} bytes, "
            f"{self.seconds:.4f}s (median of {len(self.samples)}, {spread}; "
            f"warm-up {self.warmup_seconds:.4f}s discarded), HTTP {self.status}"
        )


@dataclass(frozen=True)
class Verification:
    """The measurements, the artifact built from them, and the verdict.

    Holds no verdict of its own. `verified` delegates to the artifact, whose
    `work_verified` is a property with no field behind it — so there is no
    attribute anywhere in this chain that a claim could be written to.
    """

    workload: Workload
    drives: tuple[Drive, ...]

    @property
    def verified(self) -> bool:
        return self.workload.work_verified

    @property
    def evidence(self) -> str:
        return self.workload.work_evidence

    def describe(self) -> str:
        lines = [f"{self.workload.id} → {self.workload.entry_point}"]
        lines.extend(f"  {drive.describe()}" for drive in self.drives)
        lines.append(f"  {self.evidence}")
        return "\n".join(lines)


# Runs in the *subject's* interpreter. Drives the candidate and reports all three
# of F6's metrics for the same invocations.
#
# `CaptureQueriesContext` is Django's own, and it is what makes the query count a
# measurement rather than an estimate: nothing outside the process can see how
# many statements a request issued. It forces DEBUG on for its duration, which is
# recorded on the drive because a subject running with DEBUG on is not the subject
# a user deploys — the count is exact and the *timing* under it is a little
# pessimistic, and that direction is the safe one for a threshold that must rise.
#
# The warm-up is not politeness. The first request through a Django stack pays
# module imports, template compilation and connection setup, and charging those
# to the small scale point is how a flat workload comes to look like a growing
# one.
_DRIVE_SOURCE = """
import json, os, sys, threading, time

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

REQUEST = json.loads(sys.argv[1])

client = Client()
for name, value in REQUEST["cookies"].items():
    client.cookies[name] = value

headers = REQUEST["headers"]
path = REQUEST["path"]

def one_pass():
    # One request, with everything measurable about it -- the warm-up included.
    # S-17.14: the warm-up used to run outside the capture, so the cold pass had a
    # duration and no query count, and `_cached_state` compares warm-up excess on
    # whichever metric the patch claims to reduce. For an N+1 that is a count, so a
    # cold pass nobody counted left the check unable to run on the metric that
    # matters.
    with CaptureQueriesContext(connection) as captured:
        started = time.perf_counter()
        response = client.get(path, headers=headers)
        elapsed = time.perf_counter() - started
    return {
        "seconds": elapsed,
        "db.query": float(len(captured)),
        "response_bytes": float(len(response.content)),
    }, response.status_code


def resources():
    # The subject's own resource levels, read here because they are the subject's.
    # `primitives.envelope` takes RUSAGE_SELF and this interpreter's allocated
    # blocks, so wrapped around this subprocess it would report what the harness
    # did while it waited.
    # Names match `primitives.envelope`'s constants deliberately: the audit
    # compares these two samples against that module's tolerances, and a second
    # spelling would silently leave every metric unwatched.
    reading = {
        "allocated_blocks": float(sys.getallocatedblocks()),
        "cpu_seconds": float(time.process_time()),
        "wall_seconds": float(time.perf_counter()),
        "thread_count": float(threading.active_count()),
    }
    try:
        import resource as _resource

        usage = _resource.getrusage(_resource.RUSAGE_SELF)
        reading["peak_rss_bytes"] = float(usage.ru_maxrss)
        reading["bytes_written"] = float(usage.ru_oublock)
    except (ImportError, AttributeError):
        pass
    try:
        reading["open_file_descriptors"] = float(len(os.listdir("/proc/self/fd")))
    except OSError:
        pass
    return reading


envelope_before = resources()

warm_started = time.perf_counter()
warm_pass, status = one_pass()
warmup = time.perf_counter() - warm_started

passes = []
for _ in range(REQUEST["repeats"]):
    measured, status = one_pass()
    passes.append(measured)

envelope_after = resources()
samples = [measured["seconds"] for measured in passes]

print("__MARKER__" + json.dumps({
    "warmup_seconds": warmup,
    "warm_pass": warm_pass,
    "passes": passes,
    "envelope_before": envelope_before,
    "envelope_after": envelope_after,
    "samples": samples,
    "queries": int(passes[-1]["db.query"]) if passes else None,
    "response_bytes": int(passes[-1]["response_bytes"]) if passes else None,
    "status": status,
}))
"""

_DRIVE = _DRIVE_SOURCE.replace("__MARKER__", _MARKER)


def _run_in_subject(  # noqa: PLR0913 - S-7.4's shape, for S-7.4's reason: what to
    # run, what to pass it, where, with which interpreter and under which settings
    # are five facts and three of them belong to the sandbox.
    program: str,
    argument: str,
    *,
    surface: Surface,
    python: Sequence[str],
    settings: str,
    timeout: float,
) -> Mapping[str, Any]:
    """Run one program in the subject's interpreter and read its answer.

    `Any` at a subprocess boundary: another interpreter's JSON. Every field is
    converted at the call site rather than trusted.
    """
    try:
        result = surface.run(
            [*python, "-c", program, argument],
            timeout=timeout,
            env={"DJANGO_SETTINGS_MODULE": settings},
        )
    except ExecutionError as error:
        raise WorkVerificationError(str(error)) from error

    line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
    if line is None:
        said = (result.stderr or result.stdout).strip()[-600:]
        message = f"the subject's interpreter did not answer (exit {result.exit_code}): {said}"
        raise WorkVerificationError(message)

    try:
        payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
    except json.JSONDecodeError as error:
        message = f"the subject's answer was not JSON: {error}"
        raise WorkVerificationError(message) from error
    return payload


def _settings_for(root: Path) -> str:
    settings = settings_module(root)
    if settings is None:
        message = (
            "no DJANGO_SETTINGS_MODULE was found in manage.py, wsgi.py or asgi.py, so the "
            "candidate cannot be driven"
        )
        raise WorkVerificationError(message)
    return settings.value


def drive(  # noqa: PLR0913 - the subject, its interpreter, what to request, with
    # which credential and how many times are five independent facts, and the
    # credential belongs to S-7.4 rather than here. None of them is a measurement:
    # that is the point of the signature.
    root: Path,
    *,
    python: Sequence[str],
    path: str,
    scale: int,
    created: Mapping[str, int],
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    repeats: int = DEFAULT_REPEATS,
    surface: Surface | None = None,
    timeout: float = DRIVE_TIMEOUT_SECONDS,
) -> Drive:
    """Invoke the candidate at the current data volume and measure it.

    Takes what is needed to *reach* the subject and nothing that could carry a
    result. The three numbers come back from the subject's own instrumentation.

    Raises:
        WorkVerificationError: the subject could not be driven, or answered with
            a status that makes the measurement meaningless.
    """
    if repeats < 1:
        message = (
            f"{repeats} repeat(s) measures nothing. Clamping it to one silently would answer a "
            "question nobody asked, and F6's wall-time condition is a ratio between medians"
        )
        raise WorkVerificationError(message)

    payload = _run_in_subject(
        _DRIVE,
        json.dumps(
            {
                "path": path,
                "headers": dict(headers or {}),
                "cookies": dict(cookies or {}),
                "repeats": repeats,
            }
        ),
        surface=surface or HostSurface(Path(root)),
        python=python,
        settings=_settings_for(root),
        timeout=timeout,
    )

    status = int(payload.get("status") or 0)
    samples = tuple(float(sample) for sample in payload.get("samples", []))
    if not samples:
        message = f"{path} was driven and reported no timing samples at n={scale}"
        raise WorkVerificationError(message)

    if not _OK_LOW <= status <= _OK_HIGH:
        message = (
            f"{path} answered HTTP {status} at n={scale}. A workload is not shown to do work by "
            "failing consistently — an error page is cheap, constant and identical at every "
            "scale, which is exactly the profile this check exists to reject"
        )
        raise WorkVerificationError(message)

    return Drive(
        scale=scale,
        queries=int(payload.get("queries") or 0),
        response_bytes=int(payload.get("response_bytes") or 0),
        # The median, not the mean: one scheduling slice on the small point moves
        # a mean enough to flip a ratio, and the wall-time condition is a ratio.
        seconds=statistics.median(samples),
        samples=samples,
        warmup_seconds=float(payload.get("warmup_seconds") or 0.0),
        status=status,
        created=dict(created),
        passes=tuple(
            {name: float(value) for name, value in measured.items()}
            for measured in payload.get("passes", [])
        ),
        warm_pass={name: float(value) for name, value in (payload.get("warm_pass") or {}).items()},
        envelope_before={
            name: float(value) for name, value in (payload.get("envelope_before") or {}).items()
        },
        envelope_after={
            name: float(value) for name, value in (payload.get("envelope_after") or {}).items()
        },
    )


_OK_LOW = 200
_OK_HIGH = 299


def verify_work(  # noqa: PLR0913 - what to drive, where, how to seed it, how the
    # data is shaped and how the state is restored are independent facts from five
    # different stories; none is derivable from the others.
    root: Path,
    *,
    python: Sequence[str],
    path: str,
    workload_id: str,
    description: str,
    reset: ResetStrategy,
    reset_between: Sequence[str] | None = None,
    scales: Sequence[int] = DEFAULT_SCALES,
    target: str | None = None,
    per_parent: int = 1,
    distribution: Distribution = Distribution.UNIFORM,
    seed: Seeder | None = None,
    environment: EnvironmentAnchor | None = None,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    repeats: int = DEFAULT_REPEATS,
    surface: Surface | None = None,
    timeout: float = DRIVE_TIMEOUT_SECONDS,
) -> Verification:
    """Seed at each scale, drive the candidate, and let the artifact decide.

    The order per scale point is reset, seed, drive. Resetting *between* points
    rather than seeding on top of the last one is what makes the second
    measurement a measurement of a hundred rows rather than of a hundred and ten,
    and it is what lets the fixture keep the shape S-7.7 asked for.

    **Nothing in this signature can carry a verdict or a metric.** The scales are
    volumes, the credential is S-7.4's, and the result is read off the artifact.

    Raises:
        WorkVerificationError: fewer than two scales, a spread too narrow for
            F6's thresholds to mean anything, or a subject that could not be
            seeded or driven. Not raised for a workload that simply fails the
            test — that is a `Verification` reporting `verified` false, and the
            difference matters because one is a broken run and the other is an
            answer.
    """
    ordered = sorted(dict.fromkeys(int(scale) for scale in scales))
    if len(ordered) < _MINIMUM_SCALES:
        message = (
            f"work verification needs at least two distinct scales, got {ordered}. One "
            "measurement of a stub route and one of a real endpoint are the same measurement"
        )
        raise WorkVerificationError(message)
    if ordered[0] < 1:
        message = f"a scale of {ordered[0]} seeds nothing to measure"
        raise WorkVerificationError(message)

    ratio = ordered[-1] / ordered[0]
    if ratio < MINIMUM_SCALE_RATIO:
        message = (
            f"{ordered[0]} to {ordered[-1]} is a {ratio:.1f}x spread, and F6's thresholds were "
            f"written against 10x. Below {MINIMUM_SCALE_RATIO:g}x they ask a workload to double "
            "its payload for a small increase in data, which rejects correct workloads — so a "
            "pass here would mean less than the refusal"
        )
        raise WorkVerificationError(message)

    if seed is None and target is None:
        message = (
            "synthesis needs a target model to seed, and no seeder was supplied either. One of "
            "the two has to say what the rows are: `target` names what to build from the schema, "
            "and `seed` is the repository's own mechanism (S-7.5, preferred where there is one)"
        )
        raise WorkVerificationError(message)

    root = Path(root)
    where = surface or HostSurface(root)
    drives: list[Drive] = []
    recipe = None

    for scale in ordered:
        _reset(where, python=python, command=reset_between, timeout=timeout)
        if seed is not None:
            seeded_recipe, created = seed(root=root, python=python, scale=scale, timeout=timeout)
        else:
            assert target is not None
            synthesized = synthesize(
                root,
                python=python,
                target=target,
                count=scale,
                per_parent=per_parent,
                distribution=distribution,
                surface=where,
                timeout=min(timeout, SYNTHESIS_TIMEOUT_SECONDS),
            )
            seeded_recipe, created = synthesized.recipe(), synthesized.created
        recipe = seeded_recipe
        drives.append(
            drive(
                root,
                python=python,
                path=path,
                scale=scale,
                created=created,
                headers=headers,
                cookies=cookies,
                repeats=repeats,
                surface=where,
                timeout=timeout,
            )
        )

    if recipe is None:  # pragma: no cover - the scale check above guarantees one
        message = "no scale point was seeded"
        raise WorkVerificationError(message)

    workload = Workload(
        id=workload_id,
        description=description,
        entry_point=path,
        fixture=recipe,
        reset_method=reset,
        environment=environment,
        observations=tuple(entry.observation() for entry in drives),
    )
    return Verification(workload=workload, drives=tuple(drives))


_MINIMUM_SCALES = 2


def _reset(
    surface: Surface, *, python: Sequence[str], command: Sequence[str] | None, timeout: float
) -> None:
    """Return the subject to its baseline between scale points.

    The command is supplied, the convention S-7.2 set: what resets *this* project
    is a fact about its tooling, and S-2.6 owns the strategies. Absent, nothing is
    reset — which is correct for a caller that has already arranged its own
    baseline and wrong to guess at.
    """
    if command is None:
        return
    del python
    result = surface.run(list(command), timeout=timeout)
    if result.exit_code != 0:
        said = (result.stderr or result.stdout).strip()[-400:]
        message = (
            f"the reset between scale points failed: {said}. Measuring the second point on top "
            "of the first makes it a measurement of both, and the growth it shows is arithmetic "
            "rather than a property of the workload"
        )
        raise WorkVerificationError(message)


def accept(verification: Verification) -> Workload:
    """AC 4: hand back the workload, or refuse it.

    **One parameter, and it is the harness's own measurements.** The criterion is
    that a failing workload is rejected *regardless of what the agent claims*, and
    the way to guarantee that is to leave a claim nowhere to enter — not to
    accept one and ignore it. There is no `claimed`, no `override`, no `force`,
    and adding one would be the defect this story exists to prevent.

    Raises:
        WorkVerificationError: the workload does not do demonstrable work. The
            message is the artifact's own evidence, which says which condition
            failed and what to do about it.
    """
    if not verification.verified:
        message = (
            f"{verification.workload.id} is rejected: {verification.evidence}\n"
            f"{verification.describe()}"
        )
        raise WorkVerificationError(message)
    return verification.workload


def evidence_of(verification: Verification) -> str:
    """The report S-7.9 emits beside the artifact and S-17.2 can publish."""
    return verification.describe()
