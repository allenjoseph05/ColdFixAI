"""Propose, act, observe — until a workload is emitted or the run gives up.

Epic 7, S-7.14. **The third instance of the pattern S-7.13 and S-8.11 closed**:
an epic's mechanism built, tested, and never given a production caller. S-7.10
built `GroundingRun` — three bounds, an attempt record, one way to succeed — and
it was constructed in `tests/explorer/test_run.py` and in no module under `src/`.
`Agent.EXPLORER` has carried `attributed=False` since the role index was written,
with a note saying that either the loop that drives it is not built or its calls
are billed to nobody. It was the first.

**What this is above, and what it is not inside.** `ground_workload` is S-7.13's
mechanical sequence: fingerprint, anchor, routes, auth, fixtures, verification,
emission — nine modules in the order a caller would use them, once each, with no
retries. Six of the nine stage predicates are things that sequence never
establishes and cannot: a checkout, an importable framework, a configuration the
framework accepts, a database that answers, applied migrations, an enumerable
route. **Those six are the loop's**, and they are exactly the ones a command can
move. The other three — auth, seed, work — are established *by* the sequence, so
the loop never asks the agent to satisfy them and never asks it to seed a
database `verify_work` is about to seed itself.

That partition is the whole shape: **repair the environment until the sequence
can run, then run it.** It is checked rather than described — `REPAIRABLE` and
`ESTABLISHED_BY_THE_SEQUENCE` partition `Stage`, so a tenth stage cannot be
quietly dropped from both.

**A refusal from the sequence ends the run rather than starting a repair.** When
all six hold and `ground_workload` still says no, what it is refusing is about
the repository's content — no drivable route, no credential, an endpoint that
does no work — and no command at `connect` or `migrate` changes any of those.
`NotGroundableError`'s own docstring makes that answer a result rather than a
fault, and `00-BRIEF.md` §9 ships it.

**The loop measures nothing and runs nothing.** `Hands` is supplied for the same
reason `Executor` is supplied to S-8.9's loop and `python` and `request` are
supplied to `ground_workload`: `CLAUDE.md` puts the measuring in the harness and
the reasoning in the agent, and a loop that could run a command itself would be
the one place a denylist, a container boundary and `03-agents.md` §2.5's
workspace confinement have to be re-implemented rather than inherited.

**The double-counted grounding step, and it disabled a bound.** `Session.run`
recorded a step for every phase whose cap is counted in steps, which is grounding
and nothing else — and `GroundingRun.attempt` records one too. Two records per
turn halves the sixty-step cap, and the *stall* check fares worse than that:
`record_step` with no conclusion **clears** the run of repeats, so a model call
between two attempts reset the counter every turn and fifteen identical stage
reports could never accumulate. The bound S-7.10 built, and AC 4 names, was
switched off by the first caller that made a model call. Six phases already count
their own unit; grounding was the seventh recorder. See ADR 139.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from coldfix.cost.accounting import Phase
from coldfix.cost.budget import BudgetExhaustedError, Disposition
from coldfix.cost.session import Session
from coldfix.explorer.compose import CompositionError, Grounded, NotGroundableError
from coldfix.explorer.emission import EmittedWorkload
from coldfix.explorer.fingerprint import Fingerprint, fingerprint
from coldfix.explorer.proposal import GiveUp, Move, Tried, propose
from coldfix.explorer.run import (
    DEFAULT_STAGE_ATTEMPTS,
    Attempt,
    Failure,
    GroundingFailedError,
    GroundingRun,
)
from coldfix.explorer.stages import Grounding, Outcome, Progress, Stage
from coldfix.llm.client import ModelClient

REPAIRABLE: frozenset[Stage] = frozenset(
    {
        Stage.CLONE,
        Stage.DEPENDENCIES,
        Stage.CONFIGURE,
        Stage.CONNECT,
        Stage.MIGRATE,
        Stage.ENDPOINT,
    }
)
"""The stages a command can move, and therefore the ones the agent is asked about.

Six of ADR 009's nine. Each is a fact about the *environment* — a checkout, an
importable framework, a configuration the framework accepts, a database that
answers, applied migrations, an enumerable route — and each has commands that
make it true."""

ESTABLISHED_BY_THE_SEQUENCE: frozenset[Stage] = frozenset({Stage.AUTH, Stage.SEED, Stage.WORK})
"""The three `ground_workload` settles for itself, which is why they are not asked.

`resolve_auth` mints the credential, and `verify_work` seeds at both scale points
and drives the route. **Asking the agent to seed would be the sharpest mistake of
the three**: the composed subject is migrated and deliberately empty, `seed`'s
predicate is false there, and a loop that treated that as an obstacle would spend
its budget filling a database that the sweep is about to fill correctly — and
then measure a scale nobody asked for."""


class LoopError(Exception):
    """The exploration could not start."""


@dataclass(frozen=True)
class Effect:
    """What the harness saw when it carried out one move.

    Two fields, because `04-cost.md` §3's mechanical check for this step is the
    **exit code** and the output is what the next question is written from. There
    is no `succeeded` the caller may set: it is derived, so a `Hands` cannot
    report a failure as a success while carrying the failing output.
    """

    exit_code: int
    output: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


type Hands = Callable[[Move], Effect]
"""How a command actually gets run. **Supplied, never taken.**

The same construction as S-8.9's `Executor` and for the same reason: the loop
sequences and the harness acts. `03-agents.md` §2.5 puts the denylist, the egress
block and the workspace confinement on the container the command runs in, and a
loop holding its own `execute` would be a second place all three have to exist.

A plain callable rather than a `Protocol`, because a protocol declaring a *named*
parameter rejects the obvious one-line callable — S-6.3 wrote that trap down and
this project has now walked into it four times."""


type BoundSequence = Callable[[], Grounded]
"""S-7.13's `ground_workload`, bound to a repository. Structurally what
`orchestrator.adapters.Grounder` already is."""


@dataclass(frozen=True)
class Exploration:
    """One repository's grounding attempt, and everything it took.

    **Exactly one of `grounded` and `failure` is set**, refused in the
    constructor rather than documented: a result carrying both would let a caller
    read the workload and ignore the reason it is not there, and a result
    carrying neither is a run that ended without saying how.
    """

    steps: int
    """Steps to the emitted workload, or to the point the run stopped. **AC 3.**

    S-13.5 plots this against the number of projects with a fingerprint, and
    until this story it was a constant: `ground_workload` runs its nine stages
    once each whatever it meets, so *steps to ground* had one value for every
    repository in the world."""

    attempts: tuple[Attempt, ...]
    tried: tuple[Tried, ...]
    grounded: Grounded | None = None
    emitted: EmittedWorkload | None = None
    failure: Failure | None = None

    def __post_init__(self) -> None:
        if (self.grounded is None) == (self.failure is None):
            message = (
                "an exploration is either a grounded repository or a failure and never both or "
                "neither. Both would let a caller read the workload past the reason it should "
                "not; neither is a run that ended without saying how"
            )
            raise LoopError(message)
        if (self.grounded is None) != (self.emitted is None):
            message = (
                "a grounded repository and its emitted workload arrive together, because `finish` "
                "is what produces the second from the first and is the only way a run succeeds"
            )
            raise LoopError(message)

    def report(self) -> str:
        head = f"Exploration: {self.steps} step(s), {len(self.tried)} command(s) run"
        if self.failure is not None:
            return f"{head}\n{self.failure.report()}"
        # Narrowing an invariant `__post_init__` has already refused the other
        # side of. A branch here would be a second answer to the same question,
        # and one no test could reach.
        assert self.emitted is not None
        return f"{head}\n{self.emitted.describe()}"


@dataclass
class _State:
    """What the loop accumulates between turns.

    The commands and their results, which `GroundingRun` deliberately does not
    hold: an `Attempt` records the agent's own label beside the harness's verdict
    and nothing else, and stuffing four hundred bytes of a failing migration into
    that label would make the failure report unreadable in order to keep the
    history somewhere.
    """

    tried: list[Tried] = field(default_factory=list)


def explore(  # noqa: PLR0913 - the repository, how to run its interpreter, the
    # sequence to run once it stands up, the hands that run a command and the two
    # measured token counts are five independent facts from four owners, plus the
    # session and the client every agent entry point takes.
    session: Session,
    client: ModelClient,
    *,
    root: Path,
    python: Sequence[str],
    ground: BoundSequence,
    hands: Hands,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    stage_attempts: int = DEFAULT_STAGE_ATTEMPTS,
) -> Exploration:
    """Drive one repository to an emitted workload, or to an honest failure. **AC 1.**

    **The three bounds are not this function's**, which is AC 4: `GroundingRun`
    refuses to be constructed against a budget whose progress check is not
    grounding's, carries S-5.4's compiled sixty-step cap, and stops a stage that
    has spent its own budget. All three are enforced by constructing the run,
    which is why the run is constructed here rather than accepted as an argument —
    a caller handing one in could hand in one built against another phase's budget.

    **Stopping is not failing.** Every way this ends without a workload returns an
    `Exploration` carrying a `Failure`; the one exception is the global euro
    ceiling, which `run_investigation` established is the run's to halt on and not
    a phase's outcome to report.

    Raises:
        NotGroundableError: the path is not a repository this system has stage
            predicates for. Refused before the first model call, because every
            question would otherwise be asked against nine `UNKNOWN` verdicts.
        BudgetExhaustedError: the global euro ceiling stopped the run. The
            per-phase caps produce a `Failure` instead.
        ProposalError: the model answered something that is not a command and is
            not a refusal. **Not caught**, and not turned into a `Failure`:
            `CLAUDE.md` forbids swallowing an exception to keep a run going, and
            *this repository will not ground* and *the model stopped answering in
            the agreed shape* send a reader to two different places. `Failure` is
            for the first.
    """
    identification = fingerprint(root)
    if not isinstance(identification, Fingerprint):
        message = (
            f"{root} is not a repository this system can ground, so there is nothing to explore.\n"
            f"{identification.describe()}\nEvery stage would report UNKNOWN forever and every "
            "question would be asked against a report with nothing in it"
        )
        raise NotGroundableError(message)

    run = GroundingRun(
        identification=identification,
        grounding=Grounding(root=root, python=python),
        budget=session.budget,
        stage_attempts=stage_attempts,
    )
    state = _State()

    while True:
        progress = run.measured or run.progress()
        blocked = blocking(progress)
        if blocked is None:
            return _run_the_sequence(run, state, ground)

        try:
            proposal = propose(
                session,
                client,
                blocked=blocked,
                progress=progress,
                tried=state.tried,
                attempts_left=stage_attempts - run.attempts_at(blocked.stage),
                steps_left=run.budget.remaining(Phase.GROUND),
                measured_prefix_tokens=measured_prefix_tokens,
                measured_prompt_tokens=measured_prompt_tokens,
            ).value
        except BudgetExhaustedError as error:
            # S-8.9's rule, unchanged: *the halt is the global ceiling's alone.* A
            # euro ceiling stops the run; a grounding cap is this phase's own
            # outcome and `ABORT` means abort with a diagnostic, which is a
            # `Failure` rather than an exception travelling past the caller.
            if error.exhaustion.disposition is Disposition.HALT:
                raise
            return _stopped(run, state, error.exhaustion.report())

        if isinstance(proposal, GiveUp):
            return _stopped(run, state, proposal.reason)

        effect = hands(proposal)
        state.tried.append(
            Tried(
                stage=blocked.stage,
                move=proposal,
                exit_code=effect.exit_code,
                output=effect.output,
            )
        )

        try:
            run.attempt(
                blocked.stage,
                what=f"{proposal.rendered()} (exit {effect.exit_code})",
                # **Zero, and not the cost of the call that has just been made.**
                # `authorize` projects the *next* spend against the ceiling, and
                # this call is already in the ledger — `session.run` authorized it
                # before making it, which is the only place a ceiling can be
                # enforced. Charging it again here would refuse a run at half the
                # ceiling it was given.
                worst_case=Decimal(0),
            )
        except GroundingFailedError as error:
            return _failed(run, state, error.failure)


def blocking(progress: Progress) -> Outcome | None:
    """The first stage the agent is asked about, or `None` when there is none left.

    **The first incomplete *repairable* one, not `first_incomplete`.** The stage
    order is ADR 009's ordinary order rather than a script, and `auth` and `seed`
    both sit ahead of `endpoint` in it — so a repository that is migrated and
    empty reports `seed` as its first incomplete stage, and a loop reading that
    number would ask an agent to seed a database `verify_work` seeds correctly
    thirty seconds later.
    """
    return next(
        (entry for entry in progress.outcomes if entry.stage in REPAIRABLE and not entry.complete),
        None,
    )


def _run_the_sequence(run: GroundingRun, state: _State, ground: BoundSequence) -> Exploration:
    """Every repairable stage holds. Run S-7.13's sequence and finish on its result.

    **`finish` is called rather than trusted around**, and it is what makes this
    the same success path S-7.10 built: it observes the verification, refuses a
    run with any stage still incomplete, and emits through S-7.8's gate. The
    sequence has already emitted an identical document — `emit` recomputes the
    verdict from the observations and holds no state — so the second call is the
    check rather than a second workload.
    """
    try:
        grounded = ground()
    except CompositionError as error:
        # Not an obstacle to repair. Every repairable predicate holds, so what is
        # being refused is the repository's content, and no command at `connect`
        # or `migrate` produces a drivable route or a credential that works.
        return _stopped(run, state, str(error))

    run.observed(auth=grounded.auth)
    try:
        emitted = run.finish(grounded.verification, reset=grounded.reset)
    except GroundingFailedError as error:
        return _failed(run, state, error.failure)

    return Exploration(
        steps=run.steps,
        attempts=tuple(run.attempts),
        tried=tuple(state.tried),
        grounded=grounded,
        emitted=emitted,
    )


def _stopped(run: GroundingRun, state: _State, reason: str) -> Exploration:
    return _failed(run, state, run.give_up(reason))


def _failed(run: GroundingRun, state: _State, failure: Failure) -> Exploration:
    return Exploration(
        steps=run.steps,
        attempts=tuple(run.attempts),
        tried=tuple(state.tried),
        failure=failure,
    )
