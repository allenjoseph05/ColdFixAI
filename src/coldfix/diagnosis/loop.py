"""Hypothesize, design, run, interpret — and switch instrument when one comes back flat.

Epic 8, S-8.7. `00-BRIEF.md` §5 calls this *the demo that justifies the whole
architecture*, and the claim it demonstrates is §1's: the fourteen methods are
mechanizable, and **choosing which one applies is the part the literature names
as requiring expertise.** Every story before this one built an instrument or a
control. This is the first one where the choosing happens.

**What is proved here, and what is not.** The loop runs real primitives against a
real subject: the measurements are the harness's, taken by `scaling.volume` and
`ablation.stub` executing against a planted defect. The *model calls* are
replayed, because `CLAUDE.md` forbids a test hitting the API. So the test
demonstrates that a rejection propagates into a different instrument, that the
harness enforces it, and that the log records it — and it cannot demonstrate that
a model would choose to switch unprompted. `run_investigation` takes the client
as a parameter for exactly that reason: the video `10-BACKLOG.md` asks for is
this same function against `AnthropicClient`, not a second implementation of it.

**AC 1 is enforced, not hoped for.** *On a rejected hypothesis, the next
hypothesis must select a different primitive where the evidence supports it* is a
property of the system, and `CLAUDE.md`'s hard-enforcement table is explicit that
a rule which must hold regardless of what an agent decides lives in code. So a
hypothesis re-proposing an instrument that has already been rejected under
unchanged conditions is **refused and re-asked**, with the refusal added to the
exclusions the next call sees.

**Re-asking is not cascading, and the distinction is the non-negotiable.** S-8.1
must never cascade — no deterministic validator exists for a hypothesis, so a
cheap model's wrong answer is caught by nothing. Re-asking calls `generate` again
at the same temperature on the same tier with a *longer exclusion list*; no
validator is supplied to `session.run`, no model changes, and the routing is
S-5.5's throughout. What makes it legitimate is that the thing being corrected is
not the hypothesis's quality — which nothing can judge — but a fact the agent can
be told: this instrument has already answered.

**"Where the evidence supports it" is S-8.5's, not a new rule.** An instrument is
re-proposable the moment a condition it was rejected under has moved — a reseed
to a skewed fixture, a higher concurrency, a wider scale. The loop asks the
exclusion register rather than keeping its own list, so there is one answer to
*has this been settled* and S-8.8's reseed reopens it for free.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from coldfix.bench.stats import Fit
from coldfix.cost.accounting import Phase
from coldfix.cost.budget import BudgetExhaustedError, Disposition, ProgressStalledError
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.design import ExperimentSpec, design
from coldfix.diagnosis.exclusions import Conditions, ExclusionRegister
from coldfix.diagnosis.hypothesis import Hypothesis, generate
from coldfix.diagnosis.interpretation import Interpretation, interpret
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict
from coldfix.diagnosis.progress import (
    PartialChain,
    ProgressError,
    Stopped,
    check_stall_configuration,
    partial_chain,
    progress_conclusion,
)
from coldfix.diagnosis.reseed import Reseeding, Seeder, reseed
from coldfix.llm.client import ModelClient
from coldfix.primitives.measurement import MetricKind
from coldfix.primitives.registry import Selection
from coldfix.screening.workload import FixtureRecipe

RETRIES_PER_HYPOTHESIS = 3
"""How often the loop will re-ask before calling a repeated proposal a stall.

Three because the refusal is fed back and a model that has been told the same
thing three times is not going to be told it a fourth: at that point the useful
signal is *this investigation has run out of instruments*, which is a result
(`00-BRIEF.md` §9 ships null results) rather than an error to retry past."""


class LoopError(Exception):
    """The investigation could not continue."""


class NoNewInstrumentError(LoopError):
    """Every instrument the agent proposed has already answered under these conditions.

    A result rather than a failure, and it says so: the honest reading is that
    this subject's applicable experiments are exhausted, not that the agent
    malfunctioned. Carries what was proposed so a reader can see whether the
    exhaustion is real or whether S-3.1 withheld something it should not have.
    """

    def __init__(self, proposed: Sequence[str], settled: Sequence[str]) -> None:
        self.proposed = tuple(proposed)
        self.settled = tuple(settled)
        super().__init__(
            f"after {len(proposed)} attempts the agent proposed only instruments already settled "
            f"under these conditions (proposed {list(proposed)}; settled {list(settled)}). That is "
            "an investigation out of applicable experiments, which is a result — not a fault"
        )


@dataclass(frozen=True)
class Measured:
    """What one experiment produced, and what the primitive knew about it.

    **S-8.12 widened this boundary and it was one line that narrowed it.**
    `Executor` returned `Mapping[str, float]`, so everything an Epic 3 result
    carried *about* its numbers was discarded here: `scale_volume` produces a
    `kinds` mapping and a `Fit` per metric, and an `Experiment` could hold
    neither. Three of Epic 9's six attacks answered `NOT_RUN` for want of them,
    and `audit/compose.py` had to take them as arguments from a caller that might
    not have them either.

    **The loop still measures nothing.** Every field here is filled by the
    harness that ran the primitive; there is no code in this module that computes
    a fit, a kind, or a number, and `CLAUDE.md` puts the measuring in the harness
    for exactly that reason.

    **Both extras default to absent, and absence is a statement.** A primitive
    that fitted nothing — an ablation, a fault injection — says so by leaving
    `fit` unset, and S-9.2 already refuses to judge a rejection that came from no
    sweep. What is *not* allowed is inferring either from the metric's name; see
    `Experiment.kinds`.
    """

    measurement: Mapping[str, float]
    kinds: Mapping[str, MetricKind] = field(default_factory=dict)
    fits: Mapping[str, Fit] = field(default_factory=dict)
    """The growth fit **per metric**, where the primitive drew one.

    S-17.12, and it was one `Fit | None` until the executor could not fill it.
    A volume sweep fits *every* metric it measured, and `audit/scales.py` reads
    `exponent` and `power_r_squared` off a single fit to raise `FIT_TOO_POOR` —
    so a noisy `seconds` fit objected to a finding whose claim was about
    `db.query`. Nothing in an `ExperimentSpec` names the metric a finding will
    rest on (the interpretation picks it, and it runs after the executor), so
    S-17.11 could only carry a fit for a sweep that fitted exactly one metric,
    which no real sweep does. **The check never ran.**

    Empty is still a real answer: an ablation draws no curve, and S-9.2 refuses
    to judge a rejection that came from no sweep.

    **Keyed by the primitive's metric name, which need not be a key of
    `measurement`, and the first version of this got that wrong.** `kinds` is
    validated against `measurement` because a kind describes one measured number.
    A fit does not: it describes a **series across scale points**, and a reader is
    free to record the points under derived names — the thesis fixture reports
    `db.query.n10`, `db.query.n20`, `db.query.n40` for a curve fitted on
    `db.query`. Requiring the two namespaces to coincide refused a correct
    reading, so there is no such check: what selects a fit is the metric the
    sweep fitted, and `_fit_for` looks it up by that name."""

    def __post_init__(self) -> None:
        unmeasured = set(self.kinds) - set(self.measurement)
        if unmeasured:
            message = (
                f"kinds were reported for {sorted(unmeasured)}, which this experiment did not "
                "measure. A kind describes a number, and one describing a number nobody took is "
                "a claim about a measurement that does not exist"
            )
            raise LoopError(message)


# What the harness does with a specification: run the primitive and hand back what
# it measured. **The loop never measures anything itself** — `CLAUDE.md` puts the
# measuring in the harness and the reasoning in the agent, and a loop that could
# produce a number would be the one place that rule is unenforceable.
type Executor = Callable[[ExperimentSpec], Measured]


@dataclass(frozen=True)
class Step:
    """One turn of the loop, with everything it produced."""

    hypothesis: Hypothesis
    spec: ExperimentSpec
    interpretation: Interpretation
    experiment: Experiment
    rejected_proposals: tuple[str, ...] = ()
    """Instruments the agent proposed on this turn that were already settled.

    Kept because an agent that had to be refused twice before switching is a
    different observation from one that switched immediately, and the thesis
    claim is about the choosing.
    """

    @property
    def verdict(self) -> Verdict:
        return self.interpretation.verdict


@dataclass
class Investigation:
    """One subject, one register of what has been ruled out, one log.

    Holds no budget and no step cap: those are S-8.9's, and inventing them here
    would guess at a shape that story owns — the fifth time this project has
    declined that guess.
    """

    session: Session
    client: ModelClient
    instruments: Selection
    source: str
    conditions: Conditions
    execute: Executor
    log: ExperimentLog = field(default_factory=ExperimentLog)
    exclusions: ExclusionRegister = field(default_factory=ExclusionRegister)
    steps: list[Step] = field(default_factory=list)
    stopped: Stopped | None = None
    """Why the investigation ended without a cause, or `None` while it is still
    running or when it found one. S-8.9's three ways to run out."""

    def __post_init__(self) -> None:
        """Wire this investigation's log into the prompt the session assembles.

        **Found by composing the epic: there were two append-only logs again.**
        `Session` builds a `PrunedLog` for the block `04-cost.md` §4 caches, and
        `ExperimentLog` wraps its own — so the session's block rendered an empty
        log forever while the real one rode in the uncached question. Epic 5's
        composition found this exact defect inside its own epic and recorded why
        it is silent: caching is a prefix match, so a log wrong in *content* is
        still append-only and still reports hits.

        S-8.4 exposed `.pruned` for precisely this join and nothing used it. One
        log, one owner, one rendering.
        """
        self.session.log = self.log.pruned

    # -------------------------------------------------------------- AC 1

    def settled_instruments(self) -> tuple[str, ...]:
        """Instruments already answered under the conditions now in force.

        Asked of S-8.5's register rather than kept here, so *has this been
        settled* has one answer and a reseed reopens it without this module
        knowing that reseeding exists.
        """
        return tuple(
            sorted(
                {
                    exclusion.experiment.primitive
                    for exclusion in self.exclusions.live(self.conditions)
                }
            )
        )

    def propose(
        self, *, measured_prefix_tokens: int, measured_prompt_tokens: int
    ) -> tuple[Hypothesis, tuple[str, ...]]:
        """Ask for the next hypothesis, refusing one that re-runs a settled instrument.

        The refusal is fed back as an exclusion sentence rather than as an error,
        because that is the vocabulary S-8.1's prompt already speaks: *do not
        propose one an exclusion has already settled unless a condition it
        depended on has changed.*

        Returns the accepted hypothesis **and what was refused on the way to it**.
        An agent that had to be told twice before switching is a different
        observation from one that switched immediately, and the thesis claim is
        about the choosing — so the refusals are carried rather than discarded.

        Raises:
            NoNewInstrumentError: every proposal named a settled instrument.
        """
        settled = self.settled_instruments()
        proposals: list[str] = []
        notes: list[str] = []

        for _ in range(RETRIES_PER_HYPOTHESIS):
            outcome = generate(
                self.session,
                self.client,
                log=self.log,
                exclusions=(*self.exclusions.render(self.conditions), *notes),
                source=self.source,
                instruments=self.instruments,
                measured_prefix_tokens=measured_prefix_tokens,
                measured_prompt_tokens=measured_prompt_tokens,
            )
            hypothesis = outcome.value
            if hypothesis.primitive not in settled:
                return hypothesis, tuple(proposals)

            proposals.append(hypothesis.primitive)
            notes.append(
                f"{hypothesis.primitive} has already answered under the conditions in force and "
                "was proposed again. Choose a different instrument, or say which condition would "
                "have to change for this one to be worth repeating."
            )

        raise NoNewInstrumentError(proposals, settled)

    # -------------------------------------------------------------- the turn

    def step(self, *, measured_prefix_tokens: int, measured_prompt_tokens: int) -> Step:
        """One full turn: propose, design, run, interpret, record.

        The order is the loop `02-architecture.md` §2.2 describes, and the
        recording happens last for S-8.4's reason — an append-only log cannot
        retract, so nothing is written until there is something complete to
        write.
        """
        hypothesis, refused = self.propose(
            measured_prefix_tokens=measured_prefix_tokens,
            measured_prompt_tokens=measured_prompt_tokens,
        )

        spec = design(
            self.session,
            self.client,
            hypothesis=hypothesis,
            instruments=self.instruments,
            source=self.source,
            log=self.log,
            measured_prefix_tokens=measured_prefix_tokens,
            measured_prompt_tokens=measured_prompt_tokens,
        ).value

        measured = self.execute(spec)

        reading = interpret(
            self.session,
            self.client,
            hypothesis=hypothesis,
            spec=spec,
            measurement=measured.measurement,
            log=self.log,
            measured_prefix_tokens=measured_prefix_tokens,
            measured_prompt_tokens=measured_prompt_tokens,
        ).value

        experiment = self.log.append(
            hypothesis=hypothesis.statement,
            primitive=hypothesis.primitive,
            rationale=hypothesis.rationale,
            target=spec.target,
            design=spec.render(),
            measurement=reading.measurement,
            verdict=reading.verdict,
            outcome=reading.outcome,
            # **Carried, not computed.** What the primitive knew about its own
            # numbers travels with them into the log, which is the whole of
            # S-8.12; the verdict and the measurement remain the interpreter's
            # and the harness's respectively.
            kinds=measured.kinds,
            fits=measured.fits,
        )

        if reading.verdict is Verdict.REJECTED:
            self.exclusions.record(experiment, self.conditions)

        step = Step(
            hypothesis=hypothesis,
            spec=spec,
            interpretation=reading,
            experiment=experiment,
            rejected_proposals=refused,
        )
        self.steps.append(step)
        return step

    # -------------------------------------------------------------- S-8.8

    def reseed(
        self,
        recipe: FixtureRecipe,
        scales: Sequence[float],
        seeder: Seeder,
        *,
        finding_id: str | None = None,
    ) -> Reseeding:
        """Ask for different data, and adopt the conditions it puts in force.

        The only thing in this system that moves a condition on purpose, which is
        what makes S-8.5's *may be reopened* a property something exercises rather
        than a property nothing reaches.

        **The conditions are adopted here and only on success**, because
        `reseed` raises without having changed anything when the seeding fails —
        see its docstring for what adopting them early would cost.

        Raises:
            PointlessReseedError: the request would move no condition.
            ReseedError: the fixtures could not be rebuilt.
            BudgetExhaustedError: the experiment cap is already reached.
        """
        outcome = reseed(
            recipe=recipe,
            scales=scales,
            current=self.conditions,
            register=self.exclusions,
            seeder=seeder,
            budget=self.session.budget,
            finding_id=finding_id,
        )
        self.conditions = outcome.after
        return outcome

    # -------------------------------------------------------------- S-8.9

    def partial_chain(self, symptom: Symptom) -> PartialChain:
        """What this investigation learned, when it did not find a cause.

        `symptom` comes from screening rather than from here: the investigation
        did not observe it, and a loop that invented one would be manufacturing
        the one part of the artifact that predates it.

        Raises:
            ProgressError: it is still running, or it confirmed something and
                owes an evidence chain instead.
        """
        if self.stopped is None:
            message = (
                "this investigation has not stopped, so there is nothing partial about it yet. "
                "A partial chain is what a run that ended without a cause has to show"
            )
            raise ProgressError(message)
        return partial_chain(
            symptom=symptom,
            stopped=self.stopped,
            conditions=self.conditions,
            experiments=self.log.experiments,
            exclusions=self.exclusions.exclusions,
        )

    def switched(self) -> bool:
        """Whether an instrument changed hands at any point. AC 3's subject."""
        return bool(self.log.switches())

    def report(self) -> str:
        """What happened, for a reader — and for the video."""
        return "\n\n".join(
            [
                self.log.describe(),
                self.log.describe_switches(),
                self.exclusions.report(self.conditions),
            ]
        )


def run_investigation(  # noqa: PLR0913 - the subject, its instruments, its
    # conditions and the way to run one experiment are four different facts, plus
    # the session and the client. None is derivable from the others.
    session: Session,
    client: ModelClient,
    *,
    instruments: Selection,
    source: str,
    conditions: Conditions,
    execute: Executor,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> Investigation:
    """Run until something is confirmed, or until the budget says to stop.

    **The same function the demo runs.** A test hands it a replaying client and a
    subject built from a planted defect; the video hands it `AnthropicClient` and
    a real repository. A second implementation for the demo would be a demo of
    the second implementation.

    **There is no `max_steps`, and its absence is S-8.9 arriving.** S-8.7 carried
    a loop bound because the caps were another story's and a `while True` around
    a paid API is not a thing to ship even briefly. The bound is now S-5.4's
    compiled cap of forty experiments, which means there is exactly one number
    that stops this and it is the one `04-cost.md` costed.

    **Stopping is not failing.** Every way this ends without a cause produces a
    result rather than an exception — `investigation.stopped` says which, and
    `partial_chain` assembles what was learned. `00-BRIEF.md` §9: null results
    ship as answers.
    """
    check_stall_configuration(session.budget)
    investigation = Investigation(
        session=session,
        client=client,
        instruments=instruments,
        source=source,
        conditions=conditions,
        execute=execute,
    )

    while True:
        try:
            # **No `authorize` here, and its absence was found by sabotage.** One
            # was written, and removing it changed no outcome: `Session.run`
            # authorizes inside its first attempt, before any spend, and that
            # check is the same one. A second call could not refuse anything the
            # first would not — S-7.4's redundant condition, collapsed.
            step = investigation.step(
                measured_prefix_tokens=measured_prefix_tokens,
                measured_prompt_tokens=measured_prompt_tokens,
            )
        except BudgetExhaustedError as error:
            # S-5.4: *the halt is the global ceiling's alone.* A euro ceiling is
            # a run-wide stop rather than this investigation's outcome, and
            # reporting it as *the experiment cap was reached* would be a false
            # statement about why the run ended.
            if error.exhaustion.disposition is Disposition.HALT:
                raise
            investigation.stopped = Stopped.CAP
            return investigation
        except NoNewInstrumentError:
            investigation.stopped = Stopped.INSTRUMENTS
            return investigation

        try:
            session.budget.record_step(
                Phase.INVESTIGATE, finding_id, progress_conclusion(step.verdict)
            )
        except ProgressStalledError:
            investigation.stopped = Stopped.STALL
            return investigation

        if step.verdict is Verdict.CONFIRMED:
            return investigation


def confirming_links(investigation: Investigation) -> tuple[Experiment, ...]:
    """The experiments a chain would be localized on. S-8.6 assembles the rest.

    **The loop does not build the chain**, and that is a refusal rather than an
    omission: S-8.6 requires a symptom, a mechanism, a site and the implicated
    files, and none of those is something this module measured — they come from
    screening, from S-3.9's localization, and from the agent. A loop that
    manufactured them to satisfy a constructor would be inventing precisely the
    parts of a finding that are hardest to check.

    What it can hand over is the half it does own: which experiments confirmed.
    """
    return tuple(
        step.experiment for step in investigation.steps if step.verdict is Verdict.CONFIRMED
    )
