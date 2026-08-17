"""One diagnosis in, one routed verdict out. **The path Epic 9 did not have.**

Epic 9 composed. Nine stories, six attacks, a verdict vocabulary and a routing
rule — and after all of them nothing could take an investigation and audit it.
Each attack was reachable from inputs a test built by hand, and none was
reachable from what the system actually produces. That is Epic 7's finding and
Epic 8's, a third time: **the criterion is met** and **the criterion is
reachable** are different claims, and only a composition tests the second.

**Three of the six attacks take an input the experiment log does not carry**, and
the reason is one line in `diagnosis/loop.py`: `Executor` returns
`Mapping[str, float]`, so everything the primitive knew *about* those numbers is
discarded at the loop boundary. Epic 3's results carry a `kinds` mapping and a
`Fit`; an `Experiment` carries neither.

- S-9.6 needs `kinds` — *supplied by the primitive that produced them, because
  `seconds_ablated` and `render.calls_baseline` are not distinguishable by
  spelling*;
- S-9.2 and S-9.4 need a `Fit` to judge the sweep behind a growth claim;
- S-9.6 also needs a way to re-run, which is the harness's and not the log's.

**So they are supplied here, and their absence produces `NOT_RUN` rather than a
guess.** This is the construction S-9.2 already chose for a missing fit — *not
every rejection came from a sweep, and inventing a fit to judge would be auditing
a curve nobody drew* — applied to the two other inputs that share its shape. It
is also the first real caller of `Outcome.NOT_RUN`, which S-9.8 added so that an
attack that did not run could not read as one that passed.

**Deriving `kinds` from the metric name is the wrong fix, and this module refuses
it in code.** `metric_kind` is a pure function of spelling whose default is
`COUNT`, and the thesis ablation reports `seconds.share_removed` — a *share of a
duration*, which it classifies as a count. S-9.6's rule is that a count moving at
all is material, so a re-run would report divergence every time, every finding
would be `unsound`, and the amended S-9.8 would route every investigation back
for more experiments for ever. That is the exact failure S-9.6's control test
exists to prevent, reached through the join instead of through the module.

**A fourth join, found while wiring the third.** The obvious way to get the
conditions an exclusion holds under is `emit.conditions_for(workload)` — the
producer Epic 8's composition added for exactly this shape. It is wrong here: a
`FixtureRecipe` holds **one** distribution, the one the run started with, and
S-8.8 moves the conditions on the `Investigation` when it reseeds. An audit
rebuilding them from the recipe reports a single fixture shape after a reseed has
swept two, so S-9.2 and S-9.3 object to a narrowness that was already fixed —
and the remedy they name is the reseed that just happened. So the conditions are
taken rather than derived.

**The audit's session is built here and the Diagnostician's is refused**, because
S-9.1's `refuse_shared_session` only fires if somebody calls it, and until now
nobody did.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from coldfix.audit.alternatives import attack as alternative_attack
from coldfix.audit.exclusions import audit_all
from coldfix.audit.fixtures import assess_fixture
from coldfix.audit.invocation import audit_session as build_audit_session
from coldfix.audit.representativeness import assess as assess_representativeness
from coldfix.audit.reproducibility import Rerun
from coldfix.audit.reproducibility import check as check_reproducibility
from coldfix.audit.scales import audit_scales
from coldfix.audit.sufficiency import assess_sufficiency, verdict_for_partial
from coldfix.audit.verdict import (
    Attack,
    AttackResult,
    Routing,
    authorize_round,
    calls_made,
    from_alternatives,
    from_exclusions,
    from_fixture,
    from_representativeness,
    from_reproducibility,
    from_scales,
    inapplicable,
    not_run,
    record_round,
    refuse_overspend,
    route,
    verdict_for,
)
from coldfix.bench.stats import Fit
from coldfix.cost.accounting import ExchangeRate, ModelCall
from coldfix.cost.session import Session
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict
from coldfix.diagnosis.progress import PartialChain
from coldfix.llm.client import ModelClient
from coldfix.primitives.measurement import MetricKind
from coldfix.screening.workload import Workload

NO_KINDS = (
    "the experiment log does not record which metrics are counts and which are durations. "
    "`Executor` returns a bare mapping of numbers, so the `kinds` every Epic 3 result carries "
    "is discarded at the loop boundary — and deriving it from the metric name is worse than "
    "not running the attack, because `metric_kind` defaults to COUNT and a duration read as a "
    "count diverges on every re-run"
)

NO_RERUN = (
    "no way to re-run an experiment was supplied. S-9.6 executes rather than compares stored "
    "numbers, and there is no parameter through which a measurement could arrive instead"
)

NO_FIT = (
    "no growth fit was supplied for the sweep behind this finding, so the span that separates "
    "linear from superlinear was not checked. The log records what was measured and not the "
    "curve fitted to it"
)

NO_SWEEP = (
    "this finding rests on no growth claim, so there is no sweep whose span could be too "
    "narrow to resolve one"
)

A_SWEEP_NEEDS = 2
"""Below this there is no sweep for S-9.4 to have an opinion about.

Deliberately **not** S-9.4's `MINIMUM_POINTS_TO_TRUST` of four. That number is
its judgement about a sweep that exists — *an audit whose bar equals the
instrument's bar is not auditing anything* — and borrowing it here would turn
three real scale points into *no growth claim was made*, which is a different
sentence and a false one. One point is not a sweep; everything above that is
S-9.4's to grade."""


def audit_scales_result(
    scales: Sequence[float],
    fit: Fit | None,
    *,
    relative_noise: float | None = None,
) -> AttackResult:
    """S-9.4's answer, or an honest account of why it has none.

    Three outcomes rather than two, and the third is the point: **no sweep** is
    `INAPPLICABLE` and **a sweep nobody fitted** is `NOT_RUN`. Folding them makes
    every ablation-based finding read as an incomplete audit, or makes an
    un-audited growth claim read as a checked one — S-3.1's *no* against *not
    known*, at the last join in the epic.
    """
    if len(set(scales)) < A_SWEEP_NEEDS:
        return inapplicable(Attack.SCALE_ADEQUACY, NO_SWEEP)
    if fit is None:
        return not_run(Attack.SCALE_ADEQUACY, NO_FIT)
    audit = (
        audit_scales(scales, fit, relative_noise=relative_noise)
        if relative_noise is not None
        else audit_scales(scales, fit)
    )
    return from_scales(audit)


def reproducibility_result(
    experiment: Experiment | None,
    rerun: Rerun | None,
    kinds: Mapping[str, MetricKind] | None,
    *,
    relative_noise: float | None = None,
) -> AttackResult:
    """S-9.6's answer, or why it could not be taken.

    `kinds` is **not** derived from the metric names when it is missing. See the
    module docstring: `metric_kind` defaults to `COUNT`, a share of a duration
    reads as a count, and S-9.6 calls any count that moved material — so a guess
    here makes every audit report divergence and every investigation loop.
    """
    if experiment is None:
        return not_run(Attack.REPRODUCIBILITY, "no experiment was nominated to re-run")
    if rerun is None:
        return not_run(Attack.REPRODUCIBILITY, NO_RERUN)
    if kinds is None:
        return not_run(Attack.REPRODUCIBILITY, NO_KINDS)

    audit = (
        check_reproducibility(experiment, rerun, kinds=kinds, relative_noise=relative_noise)
        if relative_noise is not None
        else check_reproducibility(experiment, rerun, kinds=kinds)
    )
    return from_reproducibility(audit)


def audit_session(
    *, source: str, rate: ExchangeRate, ceiling_eur: Decimal | None = None
) -> Session:
    """The auditor's own session. S-9.1's, re-exported so callers do not improvise.

    Present because a caller holding the Diagnostician's session is one import
    away from passing it, and `refuse_shared_session` only fires if the wrong
    object reaches `invoke` — which is late, and after the routing decision has
    already been shaped by a prompt carrying the investigator's framing.
    """
    return build_audit_session(rate=rate, source=source, ceiling_eur=ceiling_eur)


def key_experiment(log: ExperimentLog) -> Experiment | None:
    """The experiment worth re-running: the one the finding rests on.

    A confirmation if there is one, because that is the measurement the whole
    chain hangs from and the one whose failure to reproduce destroys the finding.
    Otherwise the last experiment that settled anything. `None` for a log that
    only narrowed, which has no load-bearing number to re-take.
    """
    confirmed = [item for item in log.experiments if item.verdict is Verdict.CONFIRMED]
    if confirmed:
        return confirmed[-1]
    settled = [item for item in log.experiments if item.verdict.settled]
    return settled[-1] if settled else None


def audit_finding(  # noqa: PLR0913 - the workload, the log, the conditions and
    # the exclusions are what the investigation produced; the session, client and
    # token counts are S-9.1's; and `fits`, `kinds` and `rerun` are the three
    # inputs the log cannot carry. None is derivable from the others — that is
    # the finding.
    session: Session,
    client: ModelClient,
    *,
    workload: Workload,
    conditions: Conditions,
    log: ExperimentLog,
    exclusions: Sequence[Exclusion],
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    fits: dict[int, Fit] | None = None,
    kinds: Mapping[str, MetricKind] | None = None,
    rerun: Rerun | None = None,
    relative_noise: float | None = None,
    finding_id: str | None = None,
) -> tuple[Routing, tuple[ModelCall, ...]]:
    """Run all six attacks over one diagnosis and route what they add up to.

    **The whole of Epic 9 performed once.** The round is recorded after the
    verdict exists, because `Phase.FINDING_AUDIT`'s cap counts rounds and S-9.8 is
    the only thing that counts them.

    **There is no `authorize_round` here, and its absence was found by sabotage.**
    One was written, and removing it changed no outcome: this path's first attack
    is a model call, `Session.run` authorizes against the same cap before any
    spend, and a second check could refuse nothing the first would not. That is
    S-7.4's redundant condition, which S-8.9 collapsed in the investigate loop for
    the same reason and on the same evidence.

    `audit_partial` **does** authorize, and the asymmetry is the point: it makes
    no model call at all, so nothing else would ever refuse it a third round.

    **`conditions` is taken, not derived, and that is a defect this composition
    found.** The obvious implementation calls `conditions_for(workload)` — the
    producer Epic 8's composition added for exactly this shape — but a workload's
    `FixtureRecipe` holds **one** distribution, and it is the one the run
    *started* with. S-8.8 moves the conditions when it reseeds, and it moves them
    on the `Investigation` rather than on the workload. An audit rebuilding them
    from the recipe would report a single fixture shape after a reseed had swept
    two, so S-9.2 and S-9.3 would object to narrowness that had already been
    fixed — and the remedy they name is the reseed that just happened. That is
    F3's shape once more: a condition read from the wrong place, and the reader
    cannot tell.

    `workload` is still needed, for the fixture *recipe* S-9.3 builds a reseed
    request from and for the scales screening drove.

    Returns the routing and every model call the audit made, so the caller can
    check AC 3's ceiling against a measured figure rather than an estimate.

    Raises:
        BudgetExhaustedError: this finding has used both its audit rounds.
        VerdictError: the audit spent more calls than `AUDIT_CALL_CEILING`.
        AuditError: the session belongs to another agent.
    """
    scales = [float(item.scale) for item in workload.observations]

    calls: list[ModelCall] = []

    alternatives = alternative_attack(
        session,
        client,
        log=log,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )
    calls.extend(alternatives.calls)
    refuse_overspend(calls)

    representativeness = assess_representativeness(
        session,
        client,
        workload=workload,
        log=log,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )
    calls.extend(representativeness.calls)
    refuse_overspend(calls)

    results = [
        from_exclusions(audit_all(exclusions, fits=fits, relative_noise=relative_noise)),
        from_fixture(assess_fixture(conditions, workload.fixture)),
        audit_scales_result(scales, _fit_for(log, fits), relative_noise=relative_noise),
        from_alternatives(alternatives.value),
        reproducibility_result(key_experiment(log), rerun, kinds, relative_noise=relative_noise),
        from_representativeness(representativeness.value),
    ]

    verdict = verdict_for(results)
    record_round(session.budget, verdict, finding_id)
    return route(verdict, session.budget, finding_id), tuple(calls)


def _fit_for(log: ExperimentLog, fits: dict[int, Fit] | None) -> Fit | None:
    """The fit behind the finding's growth claim, if the caller supplied one.

    Keyed by experiment index, the same way S-9.2 takes them, so a caller holding
    one mapping does not have to hold two.
    """
    if not fits:
        return None
    for experiment in reversed(log.experiments):
        if experiment.index in fits:
            return fits[experiment.index]
    return None


def audit_partial(
    chain: PartialChain,
    session: Session,
    *,
    fits: dict[int, Fit] | None = None,
    relative_noise: float | None = None,
    finding_id: str | None = None,
) -> Routing:
    """Route a run that found nothing. S-9.9, through the same round accounting.

    **No client and no model call**, which is S-9.9's decision rather than an
    omission here: S-0.8 measured a model declining to stop sixty times out of
    sixty, so the stopping decision is the harness's. The session is taken for
    its budget alone.

    **This is why `authorize_round` exists.** `audit_finding` does not need it —
    `Session.run` authorizes against the same cap before its first call — but this
    path never reaches a `Session.run`, so without the check here a caller could
    audit the same partial chain for ever against a two-round cap. The cap S-9.8
    revived would be decorative again, on the one path that spends nothing and
    therefore has nothing else watching it.
    """
    authorize_round(session.budget, finding_id)
    audit = assess_sufficiency(chain, fits=fits, relative_noise=relative_noise)
    verdict = verdict_for_partial(audit)
    record_round(session.budget, verdict, finding_id)
    return route(verdict, session.budget, finding_id)


def audit_cost(calls: Sequence[ModelCall]) -> int:
    """How many model calls one audit made. AC 3's figure, measured."""
    return calls_made(calls)
