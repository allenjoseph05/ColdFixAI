"""Epic 5 in one call: a model call that is routed, budgeted, cached and billed.

The epic's goal, from the backlog, is *make development fast and production
affordable*. Nine stories build the parts — a replay cache, a ledger, caps, a
router, a cascade, a cacheable prompt, a pruned log, a vendor cost model — and
until this module existed there was no way to spend money through them. A caller
had to run seven objects in the right order, and three of the joins had no
correct form at all.

**The four defects that only showed here.**

*Two append-only logs, and no correct way to join them.* S-5.7's `Investigation`
appends a line; S-5.8's `PrunedLog` renders a block. Appending each summary drops
S-5.8's retrieval notice, so the agent is never told the detail can be fetched
and the deferred detail is lost in practice — information preserved and never
retrieved, which is the failure S-5.8 was written to prevent. Appending the
rendered block after each experiment re-appends every earlier experiment with it,
so a 40-experiment investigation carries the log 40 times. **Both keep the
byte-prefix property**, so the cache still hits and neither reads as a failure.
The log now has one owner (`LogSource`) and the other object refuses to hold one.

*Caches are model-scoped, and nothing respected that.* S-5.9 records it as a fact
— *switching model within a run discards the cache, which S-5.5's routing has to
respect* — and no code did. One `Investigation` binds one model at construction
while the router picks a model per step and the cascade escalates mid-step, so
the obvious composition sends one prompt to three models and calls the result a
cache. `Session` holds **one prompt per model** and reports hit rates per model,
because two models are two caches and a blended figure is an average over
something that does not exist.

*A cascade spends up to three times what was authorized.* S-5.4's whole argument
is that a ceiling checked after the call is a report rather than a ceiling — and
`cascade` makes up to three calls while `authorize` was built to price one. Worse
than 3x: the third attempt runs a tier dearer, so even a caller who multiplied by
the attempt count would still under-price it. Authorization now happens inside
the attempt, at the model that attempt actually uses.

*`frontier_share` cannot see the calls that reach the frontier.* It maps
(phase, class) through the router, and escalation is not a routing decision — so
the one path S-5.6 guarantees exists is the one path the metric is blind to. The
share is measured from the models the ledger recorded instead.

Nothing here calls a model. The caller supplies what the API returned; this
prices it, counts it, and refuses the next one when the budget is gone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from coldfix.cost.accounting import (
    Agent,
    ExchangeRate,
    Ledger,
    ModelCall,
    Phase,
    RunReport,
    StepClass,
    TokenUsage,
    total_of,
)
from coldfix.cost.budget import DEFAULT_STALL_AFTER, Budget, StepUnit, worst_case_usd
from coldfix.cost.cascade import EscalationLog, cascade, dearer_than
from coldfix.cost.context import Block, Investigation, Viability
from coldfix.cost.pruning import PrunedLog
from coldfix.cost.routing import Router, StepType, Tier, classify
from coldfix.cost.vendors import (
    ANTHROPIC,
    VendorProfile,
    WorkloadShape,
    caches_at_all,
    effective_input_usd_per_mtok,
)


class SessionError(Exception):
    """A step could not be run under the epic's controls."""


@dataclass(frozen=True)
class Step:
    """What a caller is about to spend money on.

    The step *type* rather than the step class, because S-5.5 derives the class
    from `04-cost.md` §3's table and a derived class cannot be misdeclared. The
    output cap is required rather than defaulted: it is half of what the ceiling
    is enforced against, and a default would make the guarantee depend on a
    number nobody at the call site chose.
    """

    step_type: StepType
    phase: Phase
    agent: Agent
    max_output_tokens: int
    finding_id: str | None = None

    @property
    def step_class(self) -> StepClass:
        return classify(self.step_type)


@dataclass(frozen=True)
class StepOutcome[T]:
    """A completed step, and everything it cost to get one."""

    value: T
    step: Step
    routed_model: str
    blocks: tuple[Block, ...]
    viability: Viability
    calls: tuple[ModelCall, ...]
    escalated: bool

    @property
    def model(self) -> str:
        """The model that produced the accepted result — not always the routed one."""
        return self.calls[-1].model

    @property
    def cost_usd(self) -> Decimal:
        return total_of(self.calls)


@dataclass(frozen=True)
class RouteEconomics:
    """What a routing decision costs per input token, once caching is priced in.

    S-5.7 records that routing a step down a tier can *raise* its effective cost,
    because the minimum cacheable prefix is not monotonic and the cheap tier's is
    the largest. That was a warning in a docstring with nothing able to check it.
    Priced against S-5.9's model it is a number, and the comparison has a control:
    the same route wins once the prompt clears the cheap model's minimum.
    """

    routed_model: str
    routed_usd_per_mtok: Decimal
    routed_caches: bool
    dearer_model: str | None
    dearer_usd_per_mtok: Decimal | None
    dearer_caches: bool

    @property
    def false_economy(self) -> bool:
        """Whether the cheaper tier costs more per input token than the one above."""
        if self.dearer_usd_per_mtok is None:
            return False
        return self.routed_usd_per_mtok > self.dearer_usd_per_mtok

    def describe(self) -> str:
        caching = "caches" if self.routed_caches else "**does not cache**"
        lines = [
            f"  {self.routed_model}: ${self.routed_usd_per_mtok:.4f}/MTok effective ({caching})"
        ]
        if self.dearer_model is not None and self.dearer_usd_per_mtok is not None:
            dearer_caching = "caches" if self.dearer_caches else "does not cache"
            lines.append(
                f"  {self.dearer_model} (a tier up): "
                f"${self.dearer_usd_per_mtok:.4f}/MTok effective ({dearer_caching})"
            )
        if self.false_economy:
            lines.append(
                "  The cheaper tier costs more here. Its minimum cacheable prefix is longer than "
                "this prompt, so it caches nothing at all while the dearer tier reads at 0.1x — "
                "which is S-5.7's hazard, priced rather than described."
            )
        return "\n".join(lines)


def route_economics(
    router: Router,
    step_type: StepType,
    shape: WorkloadShape,
    *,
    phase: Phase | None = None,
    profile: VendorProfile = ANTHROPIC,
) -> RouteEconomics:
    """Price a routing decision against the tier above it, with caching in effect.

    Raises:
        VendorError: the profile has no price or no recorded minimum prefix for
            one of the models — which is the case S-5.9 refuses rather than
            defaults, since below an unknown minimum nothing caches silently.
    """
    routed_tier = router.tier_for(classify(step_type), phase)
    routed_model = router.tier_models[routed_tier]

    dearer_tier = dearer_than(routed_tier)
    dearer_model = None if dearer_tier is None else router.tier_models[dearer_tier]

    return RouteEconomics(
        routed_model=routed_model,
        routed_usd_per_mtok=effective_input_usd_per_mtok(profile, routed_model, shape),
        routed_caches=caches_at_all(profile, routed_model, shape),
        dearer_model=dearer_model,
        dearer_usd_per_mtok=(
            None
            if dearer_model is None
            else effective_input_usd_per_mtok(profile, dearer_model, shape)
        ),
        dearer_caches=dearer_model is not None and caches_at_all(profile, dearer_model, shape),
    )


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Session:
    """One investigation's spending, under every control Epic 5 builds.

    Holds **one prompt per model**, because a prompt cache is scoped to a model
    (S-5.9's `CachePolicy.scope`) and the router hands out several within a run.
    They share one log, since the log's *text* is the same everywhere — it is the
    cache entries that are not.
    """

    system: str
    playbook: str
    source: str
    rate: ExchangeRate
    ceiling_eur: Decimal | None = None
    stall_after: int = DEFAULT_STALL_AFTER
    """How many identical conclusions in a row count as a stalled phase.

    Passed through rather than fixed, because `Session` is the only thing that
    constructs the `Budget` and a phase needing its own value would otherwise
    have no way to ask for one. S-7.10 set grounding's at 15 and S-8.9 sets an
    investigation's at 8; the default is a default, not any phase's answer."""

    router: Router = field(default_factory=Router)
    ledger: Ledger = field(default_factory=Ledger)
    log: PrunedLog = field(default_factory=PrunedLog)
    escalations: EscalationLog = field(default_factory=EscalationLog)
    clock: Callable[[], datetime] = _now

    budget: Budget = field(init=False)
    _prompts: dict[str, Investigation] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.budget = Budget(
            ledger=self.ledger,
            rate=self.rate,
            ceiling_eur=self.ceiling_eur,
            stall_after=self.stall_after,
        )

    # ------------------------------------------------------------------ prompts

    def prompt_for(self, model: str) -> Investigation:
        """This model's prompt, built once and reused.

        A second model gets a second prompt rather than a re-pointed one: its
        cache is a different cache, its first call pays the write premium again,
        and its hit rate is a different number. Sharing one object would report
        an average over two caches, which is not a rate of anything.
        """
        if model not in self._prompts:
            self._prompts[model] = Investigation(
                system=self.system,
                playbook=self.playbook,
                source=self.source,
                model=model,
                log_source=self.log.render,
            )
        return self._prompts[model]

    @property
    def models_used(self) -> tuple[str, ...]:
        return tuple(sorted(self._prompts))

    def log_experiment(self, primitive: str, target: str, outcome: str, detail: str) -> None:
        """Record one experiment. Every model's prompt grows by the same bytes."""
        self.log.append(primitive=primitive, target=target, outcome=outcome, detail=detail)

    # --------------------------------------------------------------------- runs

    def run[T](  # noqa: PLR0913 - the measured counts are two different numbers
        # (a prefix and a whole prompt) and collapsing them is the conflation this
        # epic keeps catching; `call` and `validate` are S-5.6's two halves, and
        # `conclusion` is what S-5.4 detects a stall from. None may be defaulted
        # without making a guarantee depend on a number nobody chose.
        self,
        step: Step,
        *,
        question: str,
        measured_prefix_tokens: int,
        measured_prompt_tokens: int,
        call: Callable[[str], tuple[T, TokenUsage]],
        conclusion: str | None = None,
        validate: Callable[[T], bool] | None = None,
    ) -> StepOutcome[T]:
        """Route a step, authorize it, assemble its prompt, run it, and bill it.

        `call` is handed a model id and returns what the API returned: the result
        and its usage. The usage is the API's own figures, never a caller's
        estimate — `CLAUDE.md` forbids an agent reporting a measurement, and a
        token count is one.

        `validate` opts the step into S-5.6's cascade where §3 says one exists.
        Every attempt is authorized separately, at the model that attempt uses,
        because a cascade makes up to three calls and the last of them runs a
        tier dearer than the one the budget was asked about.

        Raises:
            SessionError: the measured prompt is shorter than its own prefix.
            BudgetExhaustedError: a cap or the ceiling stopped the next attempt.
            ProgressStalledError: the phase concluded the same thing too often.
            NoValidatorError: a validator was supplied for a step §3 says has none.
            NoDearerTierError: the result failed its check on the dearest tier.
        """
        if measured_prompt_tokens < measured_prefix_tokens:
            message = (
                f"the prompt measures {measured_prompt_tokens} tokens and its own prefix measures "
                f"{measured_prefix_tokens}, which cannot be: the prefix is part of the prompt. "
                "One of these is the other's number — the prefix decides whether anything caches, "
                "the whole prompt decides what the call is authorized against"
            )
            raise SessionError(message)

        routed_model = self.router.route(step.step_type, step.phase)
        prompt = self.prompt_for(routed_model)
        blocks = prompt.render(question)
        viability = prompt.viability(measured_prefix_tokens)

        calls: list[ModelCall] = []

        def attempt(model: str) -> T:
            self.budget.authorize(
                step.phase,
                step.finding_id,
                worst_case_usd(model, measured_prompt_tokens, step.max_output_tokens),
            )
            value, usage = call(model)
            calls.append(self._record(step, model, usage))
            return value

        if validate is None:
            value = attempt(routed_model)
            escalated = False
        else:
            cascaded = cascade(
                step.step_type,
                attempt=attempt,
                validate=validate,
                router=self.router,
                phase=step.phase,
                log=self.escalations,
            )
            value = cascaded.value
            escalated = cascaded.escalated

        # **Only where the phase's cap counts model calls**, and S-5.4 predicted
        # the defect this fixes in its own docstring: *§12.1 budgets 120 model
        # calls per finding in investigate against a cap of 40 experiments — so
        # an experiment is about three calls, and a cap counted in calls would
        # halt investigation at a third of its intended budget.* This line
        # counted every call, so the forty-experiment cap was a thirteen-
        # experiment cap until S-8.9 ran a whole loop against it.
        #
        # A phase counted in experiments, attempts or rounds has its unit counted
        # by whoever owns that unit — for investigate, S-8.9's loop, which is the
        # only thing that knows when an experiment finished.
        if self.budget.caps[step.phase].unit is StepUnit.STEP:
            self.budget.record_step(step.phase, step.finding_id, conclusion)

        return StepOutcome(
            value=value,
            step=step,
            routed_model=routed_model,
            blocks=blocks,
            viability=viability,
            calls=tuple(calls),
            escalated=escalated,
        )

    def _record(self, step: Step, model: str, usage: TokenUsage) -> ModelCall:
        """Bill one call to the ledger, and to the cache it actually used."""
        record = ModelCall(
            phase=step.phase,
            agent=step.agent,
            step_class=step.step_class,
            model=model,
            usage=usage,
            at=self.clock(),
            finding_id=step.finding_id,
        )
        self.ledger.record(record)
        self.prompt_for(model).record(usage)
        return record

    # ------------------------------------------------------------------ reports

    def observed_frontier_share(self) -> Decimal:
        """What share of the calls actually ran on the frontier model.

        Measured from the models the ledger recorded, not derived by mapping
        (phase, class) back through the router — which is what `frontier_share`
        does, and why it cannot see an escalation. S-5.6 escalates one rung on a
        failed check, so a mechanical step that S-5.5 never routes to the
        frontier tier can still *reach* it; a share computed from routes reports
        that as zero, and the drift the figure exists to catch is exactly the
        drift it would miss.
        """
        if not self.ledger.calls:
            return Decimal(0)
        frontier_model = self.router.tier_models[Tier.FRONTIER]
        on_frontier = sum(1 for call in self.ledger.calls if call.model == frontier_model)
        return Decimal(on_frontier) / Decimal(len(self.ledger.calls))

    def cache_report(self) -> str:
        """Hit rates, one model at a time.

        Never blended across models. A cache entry is scoped to the model that
        wrote it, so a run that used two models has two caches, each of which
        paid its own cold write — and one figure over both would flatter the
        second model by crediting it with the first one's warm calls.
        """
        if not self._prompts:
            return "Cache: no prompts assembled."
        lines = [f"Cache, per model ({len(self._prompts)} used; each is a separate cache):"]
        for model in self.models_used:
            for line in self._prompts[model].report().splitlines():
                lines.append(f"  {model}: {line}" if line.startswith("Cache") else f"  {line}")
        return "\n".join(lines)

    def report(self, confirmed_findings: int) -> str:
        """The epic's whole output: what the run cost, and per what.

        `confirmed_findings` comes from E9's audit rather than from here, for
        S-5.3's reason — a ledger that decided which findings counted would be
        grading its own denominator.
        """
        run = RunReport(ledger=self.ledger, confirmed_findings=confirmed_findings, rate=self.rate)
        return "\n".join(
            [
                run.render(),
                self.budget.report(),
                f"Frontier share: {self.observed_frontier_share():.0%} of calls "
                f"(escalations included)",
                self.escalations.report(),
                self.cache_report(),
                self.log.report(),
            ]
        )


def call_counts(ledger: Ledger) -> Mapping[tuple[Phase, StepClass], int]:
    """Calls per phase and class, for `frontier_share`'s hand-kept argument.

    Exists so the two figures have one source. `frontier_share` takes counts a
    caller maintains alongside the ledger, and two records of the same thing are
    two things that can disagree — S-5.3's `reconciles` argument, applied to the
    number that says whether the routing is still working.
    """
    counts: dict[tuple[Phase, StepClass], int] = {}
    for call in ledger.calls:
        key = (call.phase, call.step_class)
        counts[key] = counts.get(key, 0) + 1
    return counts
