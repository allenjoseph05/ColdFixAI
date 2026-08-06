"""What experiments exist, and which of them this project can actually support.

Epic 3, S-3.1. **This module ships the mechanism and no primitives**, the same
split ADR 013 made for counters: the registry lands here, the fourteen
experiment types land in S-3.2 onward, and adding the fifteenth is a
registration rather than a refactor.

A primitive declares four things — a name, the capabilities its environment must
provide, a cost band, and a predicate over what is known about the subject. The
first three are mechanical. The fourth is where this module earns its place,
because the obvious implementation of it is wrong in a way this project has
already been bitten by.

**An applicability predicate has three answers, not two.** *Yes*, *no*, and *not
known yet* are different, and collapsing the third into either of the other two
produces a specific failure:

- Collapsed into *yes*, the Diagnostician runs an instrument that does not
  apply. `08-audit.md` F7 is the worked example: proportional perturbation on
  single-threaded code does not fail, it degenerates into ablation and returns
  numbers. Longitudinal on a CLI tool does not fail either — it runs for hours
  and fits a flat line, which reads as *no ramp*, which is an exclusion, and
  `00-BRIEF.md` §9 says exclusions ship as findings. That is ADR 013's
  catastrophe with a different instrument: a measurement that is missing
  presented as a measurement that came back empty.
- Collapsed into *no*, the instrument silently vanishes from the list and the
  agent concludes it has exhausted the applicable experiments. `08-audit.md`
  closes on exactly this: *the agent cannot know what it does not know.*

So an unknown fact yields `UNDETERMINED`, the primitive is withheld **with its
reason recorded**, and both halves — what is offered and what was held back —
are renderable. Withholding is not the same as absence and is never silent.

**A selection is a snapshot and cannot change mid-run.** ADR 002: tools render
at position 0 of the request, prompt caching is a prefix match, and a tool list
that gains or loses an entry mid-investigation invalidates every cached
breakpoint after it. `select()` therefore copies what it read, and registering a
primitive afterwards cannot alter a `Selection` already handed out. The
consequence is deliberate and worth stating plainly: learning a fact partway
through an investigation does **not** unlock an instrument for that
investigation. It unlocks it for the next one.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

# A primitive's parameters belong to the primitive. Naming a shared signature
# here would make S-3.2 inherit a call shape it did not choose, and the
# tool-call schema the Diagnostician dispatches through is E8's to define — the
# same reason S-2.9's `classify()` takes two strings instead of a finding
# object. `object` rather than `Any`: the registry never inspects a result.
PrimitiveRun = Callable[..., object]

# A name renders into a prompt prefix that is cached across an entire
# investigation, so it is validated rather than trusted. Dotted, lowercase,
# stable: `scaling.volume`, `observation.on_cpu`.
_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class CostClass(StrEnum):
    """Order-of-magnitude wall clock for one experiment.

    Named for the unit rather than for a judgement, because "cheap" is already
    ambiguous in this project's own documents: `01-primitives.md` §2 calls
    scaling the cheapest primitive on the grounds that counts need no warmup,
    interleaving or statistical test, while §5 and §14 each call a different
    primitive the most expensive on the grounds of wall clock. Those are two
    scales. This one is wall clock, which is what an agent choosing the next
    experiment under a step cap is actually spending.
    """

    SECONDS = "seconds"
    MINUTES = "minutes"
    TENS_OF_MINUTES = "tens of minutes"
    HOURS = "hours"


_COST_ORDER: dict[CostClass, int] = {
    CostClass.SECONDS: 0,
    CostClass.MINUTES: 1,
    CostClass.TENS_OF_MINUTES: 2,
    CostClass.HOURS: 3,
}


class Capability(StrEnum):
    """What the harness and the framework adapter must supply for a primitive to run.

    A property of *this environment*, not of the subject — the distinction
    matters because the two gate independently. Load needs a load generator
    (capability) *and* a subject that serves concurrent requests (fact); either
    one missing withholds the primitive, and the two absences call for different
    actions from whoever reads the report.
    """

    EVENT_COUNTERS = "event counters"
    STACK_CAPTURE = "per-event stack capture"
    OFF_CPU_TIMING = "off-CPU timing"
    INSTRUCTION_COUNTING = "retired-instruction counting"
    FIXTURE_SEEDING = "seeding fixtures at a chosen scale"
    FIXTURE_SHAPING = "seeding fixtures with a chosen distribution"
    STATE_RESET = "verified state reset between runs"
    DIAGNOSTIC_WORKTREE = "a diagnostic worktree that cannot produce a patch"
    LOAD_GENERATION = "concurrent load generation"
    REVISION_HISTORY = "checkout of earlier revisions"
    DEPENDENCY_INTERPOSITION = "interposing on a declared dependency"
    INPUT_MUTATION = "an input mutation engine"


class ProjectFact(StrEnum):
    """What is known about the subject. Absent from a profile means *not known*.

    Every member exists because a backlog story names it as a gate, not because
    it seemed like a useful thing to record. Adding one is a line here and a line
    in the primitive that needs it; neither is agent code.
    """

    # S-3.14 / `08-audit.md` F7. Coz's virtual speedup pauses concurrently
    # running threads; with none to pause the primitive collapses into ablation.
    RUNS_CONCURRENT_CODE = "runs concurrent code within a single request"
    # S-3.12, and `01-primitives.md` §4 — load and stress need a subject that can
    # be driven past capacity, which a library or a CLI tool cannot be.
    SERVES_CONCURRENT_REQUESTS = "serves concurrent requests"
    # S-3.15. The most expensive primitive, and meaningless on a process that
    # exits.
    LONG_RUNNING_PROCESS = "runs as a long-lived process"
    # S-3.17 / `01-primitives.md` §14. Input search is for worst-case inputs an
    # attacker or a user chooses.
    PARSES_UNTRUSTED_INPUT = "parses user-controlled input"
    # S-3.16 / `01-primitives.md` §15. Nothing to degrade in a self-contained
    # batch job.
    HAS_EXTERNAL_DEPENDENCIES = "depends on external services"


class Applicability(StrEnum):
    """Whether a primitive is offered, and if not, what kind of *not*.

    Four states rather than two, because the reader's next action differs for
    each: run it; supply the missing capability; go and find out the fact; never
    ask again for this subject.
    """

    APPLICABLE = "applicable"
    UNSUPPORTED = "unsupported here"
    UNDETERMINED = "undetermined"
    NOT_APPLICABLE = "not applicable"


# Precedence when several conditions gate one primitive: a definite *no* wins
# over an *unknown*. If one required fact is known false, the primitive never
# applies to this subject whatever the unknown one turns out to be, and saying
# UNDETERMINED there would send somebody to measure a fact that cannot change
# the answer.
_DECISIVENESS: dict[Applicability, int] = {
    Applicability.NOT_APPLICABLE: 0,
    Applicability.UNSUPPORTED: 1,
    Applicability.UNDETERMINED: 2,
    Applicability.APPLICABLE: 3,
}


class PrimitiveError(Exception):
    """A primitive could not be registered, found, or used."""


class RegistrationError(PrimitiveError):
    """A primitive could not be registered."""


class UnknownPrimitiveError(PrimitiveError):
    """No primitive is registered under that name.

    Carries the names that do exist, for the same reason `UnknownHookError`
    does: the realistic cause is a near-miss, and the diagnosis is the
    difference between the two strings.
    """

    def __init__(self, name: str, available: tuple[str, ...]) -> None:
        self.name = name
        self.available = available
        known = ", ".join(available) if available else "none"
        super().__init__(f"no primitive named {name!r}; registered: {known}")


class PrimitiveUnavailableError(PrimitiveError):
    """A primitive exists but was withheld from this selection.

    Distinct from `UnknownPrimitiveError` because the causes are unrelated — one
    is a typo, the other is a subject or an environment that cannot support the
    experiment — and the message carries the recorded reason so that the
    withholding stays visible at the point somebody tries to route around it.
    """

    def __init__(self, withheld: Withheld) -> None:
        self.withheld = withheld
        super().__init__(
            f"{withheld.primitive.name} is not available for this project: "
            f"{withheld.verdict.applicability.value} — {withheld.verdict.reason}"
        )


@dataclass(frozen=True)
class Verdict:
    """An applicability answer and why it came out that way.

    The reason is not decoration. Three of the four states end with a primitive
    withheld, and a withheld instrument with no stated reason is indistinguishable
    from one that was never written.
    """

    applicability: Applicability
    reason: str = ""

    @property
    def applicable(self) -> bool:
        return self.applicability is Applicability.APPLICABLE


@dataclass(frozen=True)
class ProjectProfile:
    """What this environment can do and what is known about the subject.

    `facts` is deliberately partial. A fact absent from the mapping is *not
    known*, which is a third answer and not a quiet `False` — see the module
    docstring for what collapsing it costs.

    Both fields are copied on construction. A caller that keeps mutating the
    dictionary it passed cannot retroactively change a selection already made
    from it, which is half of what makes a `Selection` a snapshot.
    """

    capabilities: AbstractSet[Capability] = frozenset()
    facts: Mapping[ProjectFact, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))

    def check(self, fact: ProjectFact, *, because: str) -> Verdict:
        """Tri-state read of one fact, phrased as an applicability verdict.

        `because` describes what the primitive needs the fact *for*, so that a
        withheld instrument explains itself in terms of the experiment rather
        than in terms of a flag name.
        """
        known = self.facts.get(fact)
        if known is None:
            return Verdict(
                Applicability.UNDETERMINED,
                f"it is not known whether this project {fact.value}, and {because}",
            )
        if known:
            return Verdict(Applicability.APPLICABLE)
        return Verdict(
            Applicability.NOT_APPLICABLE,
            f"this project is not one that {fact.value}, and {because}",
        )


# A predicate reads a profile and answers. It never reports a missing
# capability: capabilities are checked mechanically by the registry, before any
# predicate runs, so that every primitive gets that check identically.
Predicate = Callable[[ProjectProfile], Verdict]


def always() -> Predicate:
    """Applicable wherever its capabilities are present. Observation, scaling."""

    def predicate(profile: ProjectProfile) -> Verdict:
        return Verdict(Applicability.APPLICABLE)

    return predicate


def requires(fact: ProjectFact, *, because: str) -> Predicate:
    """Applicable only where `fact` is known true.

    The combinator exists so that the common case is declared rather than
    written. A hand-rolled `profile.facts.get(fact, False)` is one character
    away from turning every unknown into a definite no, which is the failure
    this module is arranged around.
    """

    def predicate(profile: ProjectProfile) -> Verdict:
        return profile.check(fact, because=because)

    return predicate


def all_of(*predicates: Predicate) -> Predicate:
    """Every condition must hold. The least decisive answer wins.

    Reporting the first failure rather than the worst one would let an
    UNDETERMINED shadow a NOT_APPLICABLE and send somebody to establish a fact
    that cannot change the outcome.
    """
    if not predicates:
        message = "all_of() needs at least one predicate; use always()"
        raise RegistrationError(message)

    def predicate(profile: ProjectProfile) -> Verdict:
        verdicts = [inner(profile) for inner in predicates]
        return min(verdicts, key=lambda verdict: _DECISIVENESS[verdict.applicability])

    return predicate


@dataclass(frozen=True)
class Primitive:
    """One experiment type, and everything needed to decide whether to offer it.

    `run` is held here rather than in a dispatch table beside the agent, which is
    what makes AC 3 true: an agent that calls `selection.get(name)` needs no
    branch per primitive and therefore no edit when a fifteenth arrives.
    """

    name: str
    summary: str
    cost: CostClass
    run: PrimitiveRun
    required_capabilities: AbstractSet[Capability] = frozenset()
    applies: Predicate = field(default_factory=always)

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            message = (
                f"{self.name!r} is not a usable primitive name; expected dotted lowercase "
                "like 'scaling.volume'"
            )
            raise RegistrationError(message)
        if not self.summary.strip():
            message = f"{self.name} has no summary, and the summary is what the agent reads"
            raise RegistrationError(message)
        object.__setattr__(self, "required_capabilities", frozenset(self.required_capabilities))

    @property
    def signature(self) -> str:
        """The call shape, rendered for the prompt's instrument list.

        Read from the callable rather than declared separately. Two statements of
        one signature drift, and the one the agent reads would be the one that is
        not executed.

        Annotations are resolved rather than printed as written, because a module
        with `from __future__ import annotations` renders `workload: 'str'` and
        one without renders `workload: str`. That would make a cached prompt
        prefix depend on an import in a file that has nothing to do with it.

        The return annotation is dropped. What an experiment *returns* is the
        result schema, which belongs to E8 and is not something a tool list can
        state in one line without being wrong.
        """
        try:
            signature = inspect.signature(self.run, eval_str=True)
        except (NameError, TypeError):
            # An annotation naming something importable only under
            # `TYPE_CHECKING`. Strip the quotes rather than print them.
            signature = inspect.signature(self.run)
            rendered = str(signature.replace(return_annotation=inspect.Signature.empty))
            unquoted = rendered.replace("'", "")
            return f"{self.name}{unquoted}"
        return f"{self.name}{signature.replace(return_annotation=inspect.Signature.empty)}"

    def verdict(self, profile: ProjectProfile) -> Verdict:
        """Whether this primitive is offered for `profile`, and why or why not."""
        missing = sorted(self.required_capabilities - profile.capabilities)
        if missing:
            needed = ", ".join(capability.value for capability in missing)
            return Verdict(
                Applicability.UNSUPPORTED,
                f"this environment does not provide {needed}",
            )
        return self.applies(profile)


@dataclass(frozen=True)
class Withheld:
    """A primitive that exists and was not offered, with the reason it was not."""

    primitive: Primitive
    verdict: Verdict

    def __str__(self) -> str:
        return f"{self.primitive.name} — {self.verdict.applicability.value}: {self.verdict.reason}"


def _ordering(primitive: Primitive) -> tuple[int, str]:
    """Cheapest band first, then by name.

    Both keys are static properties of the primitive, so the order is the same
    on every run — which ADR 002 requires of anything rendered into a cached
    prompt prefix. Cost leads because `01-primitives.md` §17 gives the same
    advice to the agent: check the cheap thing first.
    """
    return _COST_ORDER[primitive.cost], primitive.name


@dataclass(frozen=True)
class Selection:
    """The instruments offered for one project, fixed at the moment it was made.

    Nothing here reads the registry again. Registering a primitive after a
    selection exists does not change that selection, because a tool list that
    grows mid-investigation invalidates the whole cached prefix behind it
    (ADR 002) — and because an agent whose instruments appear and disappear
    cannot be reasoned about afterwards.
    """

    profile: ProjectProfile
    available: tuple[Primitive, ...]
    withheld: tuple[Withheld, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(primitive.name for primitive in self.available)

    def get(self, name: str) -> Primitive:
        """The primitive to run, or a refusal that says which kind of refusal it is.

        Raises:
            PrimitiveUnavailableError: registered, but withheld from this
                selection. Carries the recorded reason.
            UnknownPrimitiveError: no such primitive anywhere.
        """
        for primitive in self.available:
            if primitive.name == name:
                return primitive
        for withheld in self.withheld:
            if withheld.primitive.name == name:
                raise PrimitiveUnavailableError(withheld)
        raise UnknownPrimitiveError(name, self.names)

    def instrument_list(self) -> str:
        """The TOOLS block of the Diagnostician's prompt (`03-agents.md` §4.3).

        Byte-stable for a given set of primitives and profile. It is the first
        thing in the request and every cached token after it depends on it not
        moving.
        """
        if not self.available:
            return "No instruments are available for this project."
        return "\n".join(
            f"{primitive.signature} [{primitive.cost.value}]\n    {primitive.summary}"
            for primitive in self.available
        )

    def withheld_notice(self) -> str:
        """What was held back and why.

        Rendered separately from the instrument list, and not callable from it.
        It exists so that an empty result reads as *these experiments were not
        run* rather than as *these experiments found nothing* — the distinction
        `00-BRIEF.md` §9 makes load-bearing by shipping null results as answers.
        """
        if not self.withheld:
            return "Every registered instrument is available for this project."
        listed = "\n".join(f"  - {item}" for item in self.withheld)
        return (
            "Not available for this project, and not callable in this run:\n"
            f"{listed}\n\n"
            "An instrument withheld as undetermined is one whose applicability was never "
            "established, not one that was tried and found irrelevant. Nothing it would have "
            "measured has been ruled out."
        )


class Registry:
    """Every primitive the system knows how to run.

    Explicitly instantiable rather than global-only, so that a test can build a
    registry without reaching into the one the real run uses. `REGISTRY` below is
    the one primitive modules register into.
    """

    def __init__(self) -> None:
        self._primitives: dict[str, Primitive] = {}

    def register(self, primitive: Primitive) -> None:
        """Add a primitive. A duplicate name raises rather than replacing.

        ADR 013's rule, for the same reason: two registrations disagreeing about
        what `scaling.volume` means produce measurements that are wrong, and
        refusing produces measurements that are missing. Missing is the
        recoverable one.
        """
        if primitive.name in self._primitives:
            message = f"a primitive named {primitive.name!r} is already registered"
            raise RegistrationError(message)
        self._primitives[primitive.name] = primitive

    def unregister(self, name: str) -> None:
        """Remove a primitive. For adapters being torn down, and for tests."""
        if name not in self._primitives:
            raise UnknownPrimitiveError(name, self.names)
        del self._primitives[name]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._primitives))

    def declared(self) -> tuple[Primitive, ...]:
        """Every registered primitive, in the order they would be rendered."""
        return tuple(sorted(self._primitives.values(), key=_ordering))

    def get(self, name: str) -> Primitive:
        """A registered primitive by name, ignoring applicability.

        For introspection and for tests. The run path goes through
        `Selection.get`, which is the one that can refuse.
        """
        try:
            return self._primitives[name]
        except KeyError:
            raise UnknownPrimitiveError(name, self.names) from None

    def select(self, profile: ProjectProfile) -> Selection:
        """Split the registered primitives into what is offered and what is not.

        The result is a snapshot: it holds primitives, not a reference back here,
        so later registrations cannot change a run's tool list.
        """
        available: list[Primitive] = []
        withheld: list[Withheld] = []
        for primitive in self.declared():
            verdict = primitive.verdict(profile)
            if verdict.applicable:
                available.append(primitive)
            else:
                withheld.append(Withheld(primitive, verdict))
        return Selection(
            profile=profile,
            available=tuple(available),
            withheld=tuple(withheld),
        )


REGISTRY = Registry()
"""The registry the running system uses. Primitive modules register into this."""
