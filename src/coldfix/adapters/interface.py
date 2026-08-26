"""What the core needs from a framework, stated once and in one place.

Epic 14, S-14.1. Everything above this module is framework-agnostic; everything
a framework knows arrives through one object.

**The interface is extracted, not invented.** Every operation named here already
exists as Django-specific code somewhere in `src/`, and every declaration is
already consumed by a core type that today receives it by hand:

| Operation | What does it now |
|---|---|
| `discover_workloads` | `explorer.entrypoints.enumerate_entry_points` |
| `seed` | `explorer.fixtures.factory_seeder`, matching `work.Seeder` |
| `run_workload` | `explorer.work.drive` |
| `run_tests` | the `suite_command` `audit.scoping.run_suite` is handed |
| `read_source` | `CandidateSession.sources` |
| `apply_patch` | `CandidateSession.apply_patch` |
| `reset_state` | the candidates `sandbox.verification.choose_reset` picks from |
| `capabilities` | the `ProjectProfile` `Registry.select` reads |

| Declaration | Who consumes it |
|---|---|
| hook points | `bench.counting.count`, via `primitives.counters.register_counter` |
| framework-internal frames | `primitives.localization.normalize(deny=...)` |
| protected paths | `sandbox.patching.PatchPolicy` |
| ORM dialect | the reset mechanisms, and `explorer.fingerprint.Orm` |

**Nothing in `src/` builds any of those four today.** No `ProjectProfile` is
constructed outside tests, no `Localizer` is constructed at all, no query hook is
registered anywhere in production, and every `PatchPolicy` is the bare default.
That is what this module is for: the four are not new ideas, they are four
things the core already reads and nobody yet supplies.

**The declarations reuse the core's own types rather than restating them.** An
adapter that returned its own notion of a protected path, or its own tally
object, would be a second vocabulary for a thing that already has one — and the
translation between two vocabularies is where a safety rule quietly stops
applying. So `patch_policy()` hands back a `PatchPolicy`, `localizer()` hands
back a `Localizer`, and `run_workload` returns the `Drive` the screening layer
already reads.

**An adapter may add protected paths and may not remove one.** `patch_policy()`
concatenates onto `DEFAULT_PROTECTED_PATTERNS` rather than taking the adapter's
list as the answer. `PatchPolicy`'s own docstring says the defaults *stand alone
so that a project without an adapter is still protected*; once adapters exist,
the way that sentence stops being true is an adapter that declares three
patterns and thereby drops thirty. There is no argument to this method and no
field on `Declarations` that widens what a patch may touch.

**The write goes through the session, not through the adapter.** `apply_patch`
and `read_source` take a `CandidateSession` — a value only `Workbench.open`
constructs — and the session's own `apply_patch` is what runs the protected-path
filter. A `DiagnosticSession` is not a subtype of `CandidateSession`, so S-2.3's
separation survives the new seam as a type error rather than as a convention.
This constrains the interface, not the implementer: an adapter is arbitrary
Python and could open a file itself. What it cannot do is be *handed* an
unguarded writer, which is the difference between a boundary and a promise.

**The adapter is the last place a measurement can be fabricated, and this module
does not prevent that.** `run_workload` returns numbers, and it has to: only the
framework knows how to count its own queries. The harness's non-negotiable —
*no finding without a measurement* — is upheld above here by schemas, and below
here by nothing at all. S-14.4's conformance suite is where a third-party
adapter is driven against a subject with a known answer; until it exists, an
adapter is trusted code and should be read as such.

**Not `@runtime_checkable`, deliberately.** `isinstance` against a Protocol
checks that eight attributes exist and nothing about their signatures, so it
would pass for any object with the right eight names — a weaker statement than
the one mypy already makes, dressed as a stronger one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from coldfix.bench.counting import Hook, unregister_hook
from coldfix.bench.execute import ExecutionResult
from coldfix.explorer.entrypoints import Enumeration
from coldfix.explorer.fingerprint import Framework, Orm
from coldfix.explorer.work import Drive
from coldfix.primitives.counters import register_counter
from coldfix.primitives.localization import Frame, Localizer
from coldfix.primitives.registry import Capability
from coldfix.sandbox.modes import CandidateSession, Session
from coldfix.sandbox.patching import DEFAULT_PROTECTED_PATTERNS, PatchPolicy
from coldfix.sandbox.reset import ResetMechanism
from coldfix.screening.workload import FixtureRecipe

ROW_COUNTING_VENDORS: frozenset[str] = frozenset({"postgresql"})
"""Database backends measured to report `cursor.rowcount` for a `SELECT`.

Here rather than in one adapter because it is a fact about the *database*, and
the second adapter needed the same answer for a different ORM — SQLAlchemy over
Postgres and the Django ORM over Postgres ask the same driver the same question.

Measured, not assumed, and short on purpose. PostgreSQL through psycopg reports
the row count for a `SELECT` and distinguishes a real zero from an unknown;
SQLite reports `-1` for every `SELECT`, so a row amount taken from it would be
zero on every read. MySQL and Oracle are absent because nobody has measured them
here, and a vendor nobody measured is refused rather than guessed either way.

The counter catalogue defines `db.query`'s amount as *rows returned by that
statement*, and `db.rows` is the same attachment read as a total. A hook that
recorded zero where the backend cannot answer would make a **guard counter read
flat while rows grew**, which is the failure `CLAUDE.md` names in its own words.
ADR 147 has the measurements.
"""

ADAPTER_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        # Its hooks are the counters. Nothing else can attach one.
        Capability.EVENT_COUNTERS,
        # `seed` at a chosen scale, and `seed` with a chosen distribution.
        Capability.FIXTURE_SEEDING,
        Capability.FIXTURE_SHAPING,
        # `reset_state` offers the mechanisms; the harness verifies them.
        Capability.STATE_RESET,
    }
)
"""The capabilities an adapter answers for. The rest belong to the harness.

`Capability`'s own docstring calls it *a property of this environment*, and the
environment has two halves. A diagnostic worktree, a load generator, an input
mutation engine and off-CPU timing are this system's, present or absent
regardless of what the subject is written in; counting a framework's queries and
resetting its database are not.

The split is stated so that `capabilities()` has an answerable question behind
it. An adapter claiming `DIAGNOSTIC_WORKTREE` would be claiming a capability
whose implementation it has never seen, and the profile that resulted would
offer a primitive on the strength of it.
"""

HARNESS_CAPABILITIES: frozenset[Capability] = frozenset(Capability) - ADAPTER_CAPABILITIES
"""Everything an adapter is not asked about, derived rather than listed.

Two hand-written lists drift, and the drift is silent in the direction that
matters: a capability in neither list is one no adapter claims and no harness
supplies, so every primitive requiring it is withheld and the report says the
environment does not provide it. Deriving the complement makes a thirteenth
`Capability` a deliberate classification instead of an omission.
"""


# There is deliberately no `AdapterError` here. Every failure this module can
# have already has an owner: a bad hook name is the counter catalogue's
# `UnknownCounterError`, a counter that is not an adapter's to supply is its
# `CounterError`, and a duplicate registration is `HookError`. An exception class
# that nothing raises is a class the first caller reaches for instead of the
# specific one, and CLAUDE.md's rule is that errors are typed and specific.


@dataclass(frozen=True)
class Subject:
    """The checkout under investigation, and the interpreter that runs it.

    Two facts that travel together through five of the eight operations. Held as
    one value rather than as two parameters because they must agree: an
    interpreter from one environment pointed at another environment's checkout
    is the failure that looks like a framework that cannot be imported.

    **It carries no measurement and no credential.** What comes back from an
    operation is a result; what goes in is only enough to reach the subject.
    """

    root: Path
    python: Sequence[str]
    """The subject's interpreter, as a command. S-7.2's convention: nothing under
    `src/` chooses one on its own account."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "python", tuple(self.python))


@dataclass(frozen=True)
class Declarations:
    """The four static facts an adapter states about its framework.

    Static in the sense that reads no repository and runs nothing: these are
    properties of Django, or of Flask, not of the project being investigated.
    The operations below are where a repository is touched.
    """

    orm: Orm
    """Which ORM the framework's data access goes through.

    The dialect a reset mechanism, a query hook and a row counter are all written
    against. Declared rather than detected because `fingerprint` reads a
    manifest and a manifest can be silent, while an adapter *is* the answer for
    the framework it implements.
    """

    hooks: Mapping[str, Hook] = field(default_factory=dict)
    """Hook points, keyed by the name in `primitives.counters.CATALOGUE`.

    Not free-form names: the catalogue decides which counters exist and which of
    them an adapter supplies, and `register_counter` refuses a name that is
    neither. Two of the catalogue's entries are framework-free and this system
    installs them; declaring one here is an error rather than an override, for
    ADR 013's reason — two answers to one question is worse than none.
    """

    internal_frames: tuple[str, ...] = ()
    """Path fragments belonging to the framework, dropped from captured stacks.

    `django/db/`, `rest_framework/`, `site-packages`. Matching is on the
    normalized path, so an adapter states fragments and never separators.
    Stripping these is what leaves a stack signature that is about the subject's
    own code — a localization that stops at the ORM's cursor names a line nobody
    can change.
    """

    protected_paths: tuple[str, ...] = ()
    """Framework-specific paths a patch may not touch, **added to the defaults**.

    Migrations, generated settings, compiled assets — files where a change is
    not a fix and may not even be tracked. See `patch_policy` for why this is an
    addition and cannot be a replacement.
    """

    def __post_init__(self) -> None:
        object.__setattr__(self, "hooks", MappingProxyType(dict(self.hooks)))
        object.__setattr__(self, "internal_frames", tuple(self.internal_frames))
        object.__setattr__(self, "protected_paths", tuple(self.protected_paths))

    def patch_policy(self) -> PatchPolicy:
        """The defaults, plus whatever this framework adds. Never fewer.

        The concatenation is the safety property. `DEFAULT_PROTECTED_PATTERNS`
        covers test suites, fixtures, runner configuration and CI on every
        project; an adapter knows about migrations and generated settings and
        knows nothing the defaults got wrong. Letting a declaration *be* the
        policy would mean a framework's list of two silently replacing a list of
        seventeen, and the visible symptom would be a patch that edits the tests
        and applies cleanly.

        Duplicates are dropped so that a rejection names the rule once, and
        order is stable: defaults first, in their own order, then the adapter's.
        """
        extra = tuple(
            pattern
            for index, pattern in enumerate(self.protected_paths)
            if pattern not in DEFAULT_PROTECTED_PATTERNS
            and pattern not in self.protected_paths[:index]
        )
        return PatchPolicy(protected=DEFAULT_PROTECTED_PATTERNS + extra)

    def localizer(
        self,
        *,
        root: Path | None = None,
        resolver: Callable[[Frame], Sequence[str]] | None = None,
    ) -> Localizer:
        """A localizer that knows which frames are the framework's.

        `root` and `resolver` are the harness's — where the subject's source can
        be read from, and how a frame maps to a dependency — so they are passed
        in rather than declared. The deny list is the only half an adapter owns.
        """
        return Localizer(deny=self.internal_frames, root=root, resolver=resolver)


@contextmanager
def installed(declarations: Declarations) -> Iterator[None]:
    """Register this adapter's hooks for the duration of a block, then remove them.

    A hook is process-global while installed and `register_hook` refuses a
    duplicate name, so an adapter whose hooks are left behind makes the next run
    in the same process fail at registration — a failure a long way from its
    cause. The block form is what makes leaving one behind take deliberate
    effort.

    **A partial registration is unwound.** If the third of four hooks is refused,
    the two already installed come out before the error leaves here. Otherwise a
    rejected declaration would poison the registry it failed to enter, which is
    the shape of bug where the second run reports a different problem from the
    first.

    Raises:
        UnknownCounterError: a name that is not in the counter catalogue.
        CounterError: a counter that is not an adapter's to supply, or a name
            that reads another counter's hook rather than being one.
        HookError: something is already registered under that name.
    """
    registered: list[str] = []
    try:
        for name, hook in declarations.hooks.items():
            register_counter(name, hook)
            registered.append(name)
        yield
    finally:
        for name in reversed(registered):
            unregister_hook(name)


class FrameworkAdapter(Protocol):
    """Everything the core needs from a framework, and nothing it does not.

    Eight operations and two declarations. An adapter is the only place in the
    system that may import a framework or know what its files are called; a
    module above this one that grows a `django` import has moved the boundary
    rather than used it.
    """

    @property
    def framework(self) -> Framework:
        """Which framework this adapter is for.

        The key `fingerprint()` produces and the campaign selects on. An adapter
        that answered for two frameworks would be two adapters sharing a
        conformance run, and S-14.4's suite is per adapter.
        """
        ...

    @property
    def declarations(self) -> Declarations:
        """Hook points, framework-internal frames, protected paths, ORM dialect."""
        ...

    def capabilities(self) -> AbstractSet[Capability]:
        """What this adapter can supply, drawn from `ADAPTER_CAPABILITIES`.

        Not the project's profile — the harness's own half is unioned in above,
        and the subject's *facts* are a third thing again that neither supplies.
        `ProjectProfile` keeps capabilities and facts apart because a missing
        load generator and a subject that serves no concurrent requests call for
        different actions from whoever reads the report.

        No subject argument, and that is the distinction being kept: whether
        this adapter can count queries is a fact about the adapter, while
        whether *this repository* has factories to seed with is a fact about the
        repository, which `discover_workloads` and `seed` establish by trying.
        """
        ...

    def discover_workloads(self, subject: Subject, *, timeout: float) -> Enumeration:
        """Every way into this repository, ranked, with the route table's standing.

        The standing matters as much as the list. A framework asked for its own
        route table answers completely; a repository whose files were parsed
        instead yields a list that is honestly incomplete, and `Enumeration`
        carries which of the two happened so that a workload nobody found is not
        mistaken for a workload that is not there.
        """
        ...

    def seed(
        self, subject: Subject, *, scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]:
        """Fill the subject with `scale` units of data, and say what was created.

        The return is `work.Seeder`'s, so an adapter can be handed where the
        Explorer already takes one. Both halves are needed and neither is
        derivable from the other: the recipe is *how* the data was made, which a
        rerun needs, and the counts are what was actually created, which is a
        measurement and may not match what was asked for.
        """
        ...

    def run_workload(  # noqa: PLR0913 - what to invoke, at what scale, over which
        # data, carrying which credential, and how many times are five independent
        # facts from four different stories. Collapsing any two into one argument
        # would make an adapter guess at the pair it was not given.
        self,
        subject: Subject,
        *,
        entry_point: str,
        scale: int,
        created: Mapping[str, int],
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
        repeats: int,
        timeout: float,
    ) -> Drive:
        """Invoke the entry point at the current data volume and measure it.

        `headers` and `cookies` are separate for `Credential.attach`'s reason:
        HTTP keeps them apart and every client takes them by different
        arguments. They are plain mappings rather than a `Credential` because a
        credential's *scheme* is Django-shaped and the thing a request carries is
        not.

        `repeats` is the harness's, not the framework's — the wall-time condition
        is a ratio between medians and the number of samples behind it is a
        measurement policy. An adapter that ran once and reported the reading as
        a median would be answering a question it was not asked.
        """
        ...

    def run_tests(
        self, session: Session, *, selection: Sequence[str] = (), timeout: float
    ) -> ExecutionResult:
        """Run the subject's own test suite in `session`, and return what happened.

        `Session` and not `CandidateSession`: running the tests is how a
        diagnostic revision is shown to have been healthy before anything was
        changed, and `audit.scoping.run_suite` needs the same command on both
        sides. An exit code is not a diff, so nothing about mode separation is
        weakened by letting both modes run one.

        The verdict is the caller's. This returns an `ExecutionResult` rather
        than a pass/fail because *the suite was already failing* and *the patch
        broke the suite* are different findings, and only a caller holding both
        revisions can tell them apart.
        """
        ...

    def read_source(self, session: CandidateSession) -> Mapping[str, str]:
        """The subject's source as it now stands, worktree-relative.

        Framework-specific because *what counts as source* is: a Django project's
        templates decide what a view renders, and an audit that read only `.py`
        would report a patch as touching nothing a caller reaches.

        `CandidateSession` for the reason that class's `sources` gives: a
        diagnostic session may run any command and therefore write any file, so
        giving it a reader would let an ablation run emit a diff to disk and hand
        it back. The absence is the enforcement, and it is kept here.
        """
        ...

    def apply_patch(self, session: CandidateSession, diff: str) -> frozenset[str]:
        """Write `diff` into `session`, and do whatever the framework needs after.

        **The write itself is `session.apply_patch`**, which is where the
        protected-path filter runs, because that is the only route by which a
        diff becomes a file. An adapter's part is what comes after — for a
        compiled language, a rebuild; for Django today, nothing. Returning the
        paths written is the session's contract and is passed through, so a
        caller recording an attempt does not re-derive them.

        Nothing here can be reached with a diagnostic session: the parameter's
        type refuses one, and there is no adapter method that takes a worktree
        path instead.

        Raises:
            ProtectedPathError: the patch touches a file that decides whether
                the patch worked.
            PatchDidNotApplyError: the patch was allowed and does not fit.
        """
        ...

    def reset_state(self, subject: Subject) -> Sequence[ResetMechanism]:
        """The ways this framework can return the subject to its baseline, cheapest
        first.

        **A provider, not the act, and the name is the backlog's.** Two things
        make a method that simply reset the wrong shape here. S-2.7 will not
        trust a reset it has not driven ten times — `choose_reset` verifies a
        mechanism against real row counts before any measurement rests on it, and
        an adapter that reset on demand would be an unverified reset wearing the
        same name. And `02-architecture.md` §1.5 requires a *fallback*: when
        rollback does not restore state, the answer is to fall back to a
        container restart, which needs a list rather than a choice already made.
        S-0.5 is the recorded instance — rollback alone failed ten times out of
        ten while passing the check the story specified.

        Cheapest first, because that is the order `choose_reset` tries them in
        and 19 ms against 163 ms against seconds is worth getting right.
        """
        ...
