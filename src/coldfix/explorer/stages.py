"""The nine stages of grounding, and a definition of done the agent cannot write.

Epic 7, S-7.11, and a **safety** story. Its *Why* is the gap ADR 009 found:
without a definition of done per stage, *an agent stuck at stage four and an
agent progressing normally are indistinguishable until the global cap fires.*

**Nothing here calls a model.** Asking Django whether it has unapplied migrations
is a subprocess, and counting how many stages hold is arithmetic.

**The taxonomy is closed and that is the whole bet.** S-0.3 ground three
deliberately dissimilar repositories by hand and produced sixteen distinct
obstacles — the Postgres driver failed three different ways across three
repositories — and **every one fell into one of nine stages, with none needing a
tenth**. The specifics never repeated; the stages repeated perfectly. That
asymmetry is what makes an unfamiliar obstacle tractable: *something went wrong*
is unbounded, and *stage four's predicate is false, here is the error* is a
bounded search against a stated success condition.

**Every predicate is computed here, and an agent's claim is an input to a check
rather than an answer.** `claim` exists and is meant to be called — an agent
saying *I think I have configured it* is the agent doing its job — but what it
returns is the harness's evaluation of that stage, and a claim about a stage
whose predicate is false is refused with the predicate's own reading attached.
This is S-7.8's rule extended to the other eight stages, as ADR 009 requires.

**Three verdicts, not two.** A predicate that cannot be evaluated is not a
predicate that failed: *migrations are unapplied* and *nothing could be asked
because the database refused the connection* send a reader to two different
places, and reporting the second as the first is S-7.2's flattened-ignorance
mistake in a new costume. `UNKNOWN` is what a stage says when the thing it would
measure is not reachable yet.

**A stage whose predicate already holds is complete without action.** ADR 009 is
explicit that the pipeline is an ordering of *predicates to satisfy*, not a
script of steps to execute — a repository shipping a seeded database has nothing
to do at `SEED`, and a harness that made it seed anyway would be adding rows to
somebody's fixture and then measuring the result.

**The seeding predicate excludes the framework's own tables, and that is not
tidiness.** `migrate` alone populates `django_content_type` and
`auth_permission`, so *row counts exceed a threshold in at least two tables* is
satisfied by a freshly migrated, entirely empty database. A predicate that
reported that repository as seeded would send the Explorer to measure nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from coldfix.bench.execute import ExecutionError
from coldfix.explorer.auth import Resolution as AuthResolution
from coldfix.explorer.entrypoints import settings_module
from coldfix.explorer.fingerprint import Fingerprint, Identification
from coldfix.explorer.registry import grounds_for
from coldfix.explorer.surface import HostSurface, Surface
from coldfix.explorer.work import Verification

STAGE_TIMEOUT_SECONDS = 300.0
"""Enough for `django.setup()` on an unfamiliar project and a migration check."""

SEED_THRESHOLD = 10
"""Rows a table must exceed for the seeding predicate to count it.

F6's small scale point, deliberately: a database is seeded when it holds enough
to be *measured*, and ten is the number S-7.8 takes its first observation at. A
threshold of one would call a repository with a single fixture row ready."""

SEED_TABLES = 2
"""How many tables must clear the threshold. ADR 009's *at least two*.

Two rather than one because a single populated table is what a migration's own
bookkeeping produces, and because the costs this system looks for live in
relationships — one table full of rows and nothing pointing at it cannot exhibit
a per-parent cost at all."""

# Applications whose rows `migrate` creates on its own. **This list is the
# seeding predicate**, not an optimisation of it: `django_content_type` and
# `auth_permission` are both populated by migrating an empty project, so
# counting every table reports a repository with no data at all as seeded, and
# the Explorer would go and measure nothing.
FRAMEWORK_APPS: frozenset[str] = frozenset(
    {
        "admin",
        "auth",
        "contenttypes",
        "sessions",
        "sites",
    }
)

_MARKER = "<<<COLDFIX-STAGES>>>"


class StageError(Exception):
    """A stage could not be evaluated, or an advance was refused."""


class Stage(StrEnum):
    """ADR 009's nine, in the order they are ordinarily satisfied.

    Ordinarily, not necessarily: auth sometimes resolves before seeding, and a
    repository shipping a seeded database skips one entirely. The order is what
    `first_incomplete` reports against and what a per-stage budget is spent
    along; it is not a script.
    """

    CLONE = "clone"
    DEPENDENCIES = "dependencies"
    CONFIGURE = "configure"
    CONNECT = "connect"
    MIGRATE = "migrate"
    AUTH = "auth"
    SEED = "seed"
    ENDPOINT = "endpoint"
    WORK = "work"

    @property
    def definition(self) -> str:
        """ADR 009's predicate, in the ADR's own words.

        Carried on the enum so that a failure report states the success condition
        it was measured against. *Stage four is not done* is a transcript; *stage
        four is not done, and done means a trivial query succeeds against the
        target database* is something a reader can act on.
        """
        return {
            Stage.CLONE: "a checkout exists and an entry point was located",
            Stage.DEPENDENCIES: "the framework imports in the target interpreter",
            Stage.CONFIGURE: "the framework's own check command exits 0",
            Stage.CONNECT: "a trivial query succeeds against the target database",
            Stage.MIGRATE: "the migration tool reports zero unapplied migrations",
            Stage.AUTH: "a credential authenticates against a protected route",
            Stage.SEED: (
                f"more than {SEED_THRESHOLD} rows in at least {SEED_TABLES} of the "
                "application's own tables"
            ),
            Stage.ENDPOINT: "at least one candidate route was enumerated",
            Stage.WORK: (
                "HTTP success, and query count, response bytes and wall time all move "
                "between N=10 and N=100"
            ),
        }[self]


class Verdict(StrEnum):
    """What a predicate said. Three, because there are three different next moves."""

    HOLDS = "holds"
    FAILS = "does not hold"
    UNKNOWN = "could not be evaluated here"

    @property
    def complete(self) -> bool:
        """Only `HOLDS` completes a stage.

        `UNKNOWN` deliberately does not. A stage nobody could measure is not a
        stage that passed, and treating ignorance as progress is how a run
        arrives at the final gate having skipped everything.
        """
        return self is Verdict.HOLDS


@dataclass(frozen=True)
class Outcome:
    """One stage, what the harness measured, and what it means.

    `detail` is prose for the same reason S-4.1's evidence is: every way of
    failing a stage calls for a different action, and the sentence is what a
    reader — or S-7.10's failure report — acts on.
    """

    stage: Stage
    verdict: Verdict
    detail: str

    @property
    def complete(self) -> bool:
        return self.verdict.complete

    def describe(self) -> str:
        return f"{self.stage.value}: {self.verdict.value} — {self.detail}"


@dataclass(frozen=True)
class Grounding:
    """Everything the predicates are allowed to look at.

    A bag of *what the harness has established*, not of what the agent believes.
    Two of the nine stages are settled by earlier harness work — S-7.4's auth
    resolution and S-7.8's verification — so those results are carried here and
    read; a stage whose evidence is absent reports `UNKNOWN` rather than failing,
    because *not asked* and *asked and refused* are different answers.
    """

    root: Path
    python: Sequence[str]
    surface: Surface | None = None
    """Where the subject-facing commands run. S-17.7.

    `None` means a `HostSurface` at `root`, which is the call every predicate made
    before this field existed — so a `Grounding` built anywhere in the tree keeps
    its behaviour, and adopting the surface is provably a no-op for the suite.
    A run that must judge what a proposed command did supplies a `SessionSurface`,
    because a command and its predicate that disagree about the filesystem cannot
    make progress."""

    auth: AuthResolution | None = None
    work: Verification | None = None
    seed_threshold: int = SEED_THRESHOLD
    seed_tables: int = SEED_TABLES
    timeout: float = STAGE_TIMEOUT_SECONDS

    def where(self) -> Surface:
        """The surface, or the host at `root`. Never `execute` directly."""
        return self.surface if self.surface is not None else HostSurface(Path(self.root))


# ================================================================== the subject probes

# Runs in the *subject's* interpreter. Three of the nine predicates are questions
# only the framework can answer, and this asks all three at once so a stage report
# costs one `django.setup()` rather than three.
_PROBE_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

answer = {"imported": False, "connected": False, "unapplied": None, "rows": {}}
problems = []

try:
    import django
    django.setup()
    answer["imported"] = True
    answer["version"] = django.get_version()
except Exception as error:
    problems.append("import: " + type(error).__name__ + ": " + str(error))
    answer["problems"] = problems
    print("__MARKER__" + json.dumps(answer))
    raise SystemExit(0)

from django.db import connection

try:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    answer["connected"] = True
except Exception as error:
    problems.append("connect: " + type(error).__name__ + ": " + str(error))

if answer["connected"]:
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        answer["unapplied"] = len(executor.migration_plan(targets))
    except Exception as error:
        problems.append("migrate: " + type(error).__name__ + ": " + str(error))

    try:
        from django.apps import apps

        for model in apps.get_models():
            label = model._meta.label
            answer["rows"][label] = {
                "count": model._default_manager.count(),
                "app": model._meta.app_label,
            }
    except Exception as error:
        problems.append("rows: " + type(error).__name__ + ": " + str(error))

answer["problems"] = problems
print("__MARKER__" + json.dumps(answer))
"""

_PROBE = _PROBE_SOURCE.replace("__MARKER__", _MARKER)


def probe(grounding: Grounding) -> Mapping[str, Any]:
    """Ask the subject the three questions only it can answer.

    Returns `Any` values: another interpreter's JSON, converted at each call site.
    A subject that cannot be started at all yields an empty answer rather than
    raising, because *the interpreter is not there* is itself the reading the
    dependencies predicate needs.
    """
    settings = settings_module(Path(grounding.root))
    overrides = {} if settings is None else {"DJANGO_SETTINGS_MODULE": settings.value}

    try:
        result = grounding.where().run(
            [*grounding.python, "-c", _PROBE],
            timeout=grounding.timeout,
            env=overrides,
        )
    except ExecutionError as error:
        return {"problems": [f"the subject's interpreter could not be started: {error}"]}

    line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
    if line is None:
        said = (result.stderr or result.stdout).strip()[-400:]
        return {"problems": [f"the subject did not answer (exit {result.exit_code}): {said}"]}

    try:
        payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
    except json.JSONDecodeError as error:
        return {"problems": [f"the subject's answer was not JSON: {error}"]}
    return payload


def _said(payload: Mapping[str, Any]) -> str:
    return "; ".join(str(problem) for problem in payload.get("problems", [])) or "no reason given"


# ================================================================== the nine predicates


def _dependencies(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    del grounding
    if payload.get("imported"):
        return Outcome(
            Stage.DEPENDENCIES,
            Verdict.HOLDS,
            f"the framework imports and reports version {payload.get('version', '?')}",
        )
    return Outcome(Stage.DEPENDENCIES, Verdict.FAILS, _said(payload))


def _connect(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    del grounding
    if not payload.get("imported"):
        return Outcome(
            Stage.CONNECT,
            Verdict.UNKNOWN,
            "the framework does not import, so nothing could open a connection",
        )
    if payload.get("connected"):
        return Outcome(Stage.CONNECT, Verdict.HOLDS, "SELECT 1 succeeded against the database")
    return Outcome(Stage.CONNECT, Verdict.FAILS, _said(payload))


def _migrate(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    del grounding
    unapplied = payload.get("unapplied")
    if unapplied is None:
        return Outcome(
            Stage.MIGRATE,
            Verdict.UNKNOWN,
            "the migration state could not be read; the database is not answering yet",
        )
    if int(unapplied) == 0:
        return Outcome(Stage.MIGRATE, Verdict.HOLDS, "the migration tool reports nothing unapplied")
    return Outcome(Stage.MIGRATE, Verdict.FAILS, f"{unapplied} migration(s) have not been applied")


def _auth(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    """S-7.4's resolution, read rather than re-run.

    The credential is minted by the story that owns credentials; this reports
    whether one was obtained for what the route actually asked for. A route
    needing nothing satisfies this stage — ADR 009's pipeline is an ordering of
    predicates to satisfy, and *nothing to authenticate against* is satisfied.
    """
    del payload
    if grounding.auth is None:
        return Outcome(
            Stage.AUTH,
            Verdict.UNKNOWN,
            "no route has been probed for what it requires, so there is nothing to answer about",
        )
    if grounding.auth.resolved:
        return Outcome(
            Stage.AUTH, Verdict.HOLDS, grounding.auth.requirement.describe().splitlines()[0]
        )
    return Outcome(
        Stage.AUTH,
        Verdict.FAILS,
        f"{grounding.auth.requirement.path} requires "
        f"{grounding.auth.requirement.scheme.name} and no credential was made for it",
    )


def _seed(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    """Rows in the application's own tables, and only its own.

    `migrate` populates `django_content_type` and `auth_permission` by itself, so
    a predicate counting every table reports a freshly migrated and entirely
    empty repository as seeded — and the Explorer would go on to measure nothing.
    """
    rows = payload.get("rows")
    if not rows:
        return Outcome(
            Stage.SEED,
            Verdict.UNKNOWN,
            "no table could be counted; the database is not answering yet",
        )

    populated = {
        label: int(entry.get("count", 0))
        for label, entry in rows.items()
        if str(entry.get("app", "")) not in FRAMEWORK_APPS
        and int(entry.get("count", 0)) > grounding.seed_threshold
    }
    if len(populated) >= grounding.seed_tables:
        named = ", ".join(f"{label}={count}" for label, count in sorted(populated.items())[:4])
        return Outcome(
            Stage.SEED,
            Verdict.HOLDS,
            f"{len(populated)} application table(s) hold more than "
            f"{grounding.seed_threshold} rows: {named}",
        )

    framework_rows = sum(
        int(entry.get("count", 0))
        for entry in rows.values()
        if str(entry.get("app", "")) in FRAMEWORK_APPS
    )
    return Outcome(
        Stage.SEED,
        Verdict.FAILS,
        f"{len(populated)} of the application's own tables hold more than "
        f"{grounding.seed_threshold} rows, and {grounding.seed_tables} are needed. "
        f"({framework_rows} row(s) exist in the framework's own tables, which migrating "
        "creates and which are not data.)",
    )


def _work(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    """S-7.8, unchanged, as ADR 009 says. The verdict is read, never recomputed."""
    del payload
    if grounding.work is None:
        return Outcome(
            Stage.WORK,
            Verdict.UNKNOWN,
            "nothing has been driven at two scales, so there is no measurement to judge",
        )
    verdict = Verdict.HOLDS if grounding.work.verified else Verdict.FAILS
    return Outcome(Stage.WORK, verdict, grounding.work.evidence)


# The predicate table. **Framework-scoped, as AC 3 requires**, and resolved
# through S-7.1's fingerprint rather than passed in: E14 adds an adapter by
# adding a key here, and there is no runtime path that reaches this mapping.
Predicate = Callable[[Grounding, Mapping[str, Any]], Outcome]
"""What every stage is: evidence in, one reading out.

A plain callable rather than a class, because a predicate has no state and
nothing to configure — and because the thing that must not exist is a way to
*supply* one, which a narrow type makes no easier and no harder than a wide one.
The enforcement is that nothing takes a `Predicate` as an argument.
"""

FRAMEWORK_NEUTRAL_PREDICATES: Mapping[Stage, Predicate] = {
    Stage.DEPENDENCIES: _dependencies,
    Stage.CONNECT: _connect,
    Stage.MIGRATE: _migrate,
    Stage.AUTH: _auth,
    Stage.SEED: _seed,
    Stage.WORK: _work,
}
"""Six of ADR 009's nine, and **the surprise this story turned up.**

`_DJANGO_PREDICATES` was named for a framework and only three of its members
were one framework's: `_clone` and `_endpoint` reach for Django's entry-point
enumerator, and `_configure` runs `manage.py check`. The other six read a
`payload` the subject was probed for — *did the framework import*, *is there an
unapplied migration*, *did the credential resolve* — and answer in terms nothing
Django-specific appears in. So they stay in core and every adapter builds on
them, rather than each one restating six identical functions and drifting.

An adapter supplies the remaining three and registers the union. `register`
refuses a table that is missing any of the nine, because `evaluate` measures all
nine and a partial table is a `KeyError` mid-run rather than partial support."""


def predicates_for(identification: Identification) -> Mapping[Stage, Predicate]:
    """AC 3: the predicates for this repository's framework.

    Resolved through the fingerprint, because *the framework's own check command*
    means a different command per framework and the nine questions are only
    answerable in a framework's own terms.

    Raises:
        StageError: a framework with no predicates. Every stage would be
            `UNKNOWN` forever, which reads as a run that never started rather
            than as a repository this system cannot ground.
    """
    if not isinstance(identification, Fingerprint):
        message = (
            f"grounding has no stages for this repository: {identification.reason} Nine "
            "predicates per framework is what E14's adapter adds, and without them every "
            "stage would report UNKNOWN forever"
        )
        raise StageError(message)

    known = grounds_for(identification.framework.value)
    if known is None:
        message = (
            f"nothing has taught this system to ground {identification.framework.value}. "
            "ADR 009's nine questions are only answerable in a framework's own terms, and an "
            "adapter is what supplies them"
        )
        raise StageError(message)
    return known.predicates


@dataclass(frozen=True)
class Progress:
    """Where a grounding run has got to, stage by stage."""

    outcomes: tuple[Outcome, ...]

    def outcome(self, stage: Stage) -> Outcome:
        found = next((entry for entry in self.outcomes if entry.stage is stage), None)
        if found is None:  # pragma: no cover - `evaluate` always returns all nine
            message = f"{stage.value} was not evaluated"
            raise StageError(message)
        return found

    @property
    def complete(self) -> bool:
        return all(entry.complete for entry in self.outcomes)

    @property
    def first_incomplete(self) -> Outcome | None:
        """The stage to work on, which is what a per-stage budget is spent against.

        The first that does not hold rather than the first that *fails*: a stage
        reporting `UNKNOWN` is one whose prerequisites are not there, and it is
        still the place where the run has stopped.
        """
        return next((entry for entry in self.outcomes if not entry.complete), None)

    @property
    def completed(self) -> tuple[Stage, ...]:
        return tuple(entry.stage for entry in self.outcomes if entry.complete)

    def describe(self) -> str:
        """What S-7.10 publishes when a run fails, and what makes it actionable."""
        lines = [f"Grounding: {len(self.completed)} of {len(self.outcomes)} stage(s) complete"]
        lines.extend(f"  {entry.describe()}" for entry in self.outcomes)
        stopped = self.first_incomplete
        if stopped is not None:
            lines.append(f"  stopped at: {stopped.stage.value}")
            lines.append(f"    done means: {stopped.stage.definition}")
            lines.append(f"    measured: {stopped.detail}")
        return "\n".join(lines)


def evaluate(identification: Identification, grounding: Grounding) -> Progress:
    """Measure all nine predicates.

    **There is no parameter here through which a predicate could be supplied**,
    which is AC 2's structural half: the table is resolved from the fingerprint,
    the evidence comes from `Grounding`, and an agent calling this gets the
    harness's reading of its repository whatever it believes about it.

    The subject is probed once for the three questions only the framework can
    answer, so a full stage report costs one `django.setup()` rather than three.
    """
    table = predicates_for(identification)
    payload = probe(grounding)
    return Progress(outcomes=tuple(table[stage](grounding, payload) for stage in Stage))


def claim(stage: Stage, identification: Identification, grounding: Grounding) -> Outcome:
    """AC 5: an agent says a stage is done, and the harness answers.

    Claiming is not forbidden — an agent reporting *I think I have configured it*
    is the agent doing its job, and refusing to hear it would only push the claim
    into prose nobody checks. What is forbidden is the claim *settling* anything:
    what comes back is this stage's predicate, measured now.

    Raises:
        StageError: the predicate does not hold. The claim was wrong, and the
            error carries what was measured and what done would have meant, so
            the next attempt is a bounded search rather than a retry.
    """
    table = predicates_for(identification)
    outcome = table[stage](grounding, probe(grounding))
    if not outcome.complete:
        message = (
            f"{stage.value} was claimed complete and its predicate {outcome.verdict.value}.\n"
            f"  done means: {stage.definition}\n"
            f"  measured: {outcome.detail}"
        )
        raise StageError(message)
    return outcome
