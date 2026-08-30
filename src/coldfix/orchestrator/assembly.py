"""Building the whole `Resources`. **The sentence S-17.1 was blocked on.**

S-17.15. Twenty-three fields, six of them the layer that reaches a live subject,
and until now no function anywhere that put them together — so a run could not be
started, let alone overspend.

**The order is forced, and the adapter is what forces it.**
`DjangoAdapter.reset_state`'s docstring names this function without knowing it:
*"`choose_reset` takes an iterable, so **a campaign** holding those facts appends
its own candidate after these two."* The campaign is the only layer holding the
database URL, the container's name and the seed SQL, so it is the only layer that
can pick a reset — and `bind`, `ground` and `measure` all take a `VerifiedReset`
they cannot produce.

So: verify the database, ask the adapter for its candidates, choose a reset
against the live subject, open the session, and only then build the six. Nothing
in that sequence can be reordered without something taking a value that does not
exist yet.

**A context manager, not a function returning a value.** `Resources` holds *"the
live objects no checkpoint can hold"*, and a diagnostic session owns a worktree
S-2.2 destroys. A `campaign_for` that returned a `Resources` would be one whose
caller has no way to know what to close, and the failure is a stranded checkout
per run rather than an error.

**Nothing here imports `coldfix.adapters`, and that is an invariant rather than
an oversight.** `test_no_core_module_imports_an_adapter` enforces it: *adapters
import the core; the core must never import an adapter*. ADR 148 §1 does say the
campaign is *"the only layer allowed to know both"* — but it files that widening
on S-14.5, which owns the boundary question, so this function takes what an
adapter supplies rather than the adapter itself. Five parameters instead of one is
the cost, and it is cheaper than eroding a layering invariant as a side effect of a
story about something else.

**Constructing a `VerifiedDatabase` is the production check.** It happens before
the workbench opens anything, which is the only ordering that makes S-2.5's
refusal worth having: a guard that fired after a container was running would be
reporting a rule it had already broken.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from coldfix.audit.cheating import Metrics
from coldfix.audit.measuring import measurer_for
from coldfix.audit.probing import probe_for
from coldfix.cost.accounting import ExchangeRate, Ledger
from coldfix.cost.budget import Budget
from coldfix.diagnosis.execution import executor_for
from coldfix.explorer.binding import grounder_for
from coldfix.explorer.compose import Plan
from coldfix.explorer.hands import hands_on
from coldfix.explorer.surface import SessionSurface
from coldfix.orchestrator.adapters import Resources, Tokens
from coldfix.orchestrator.campaign import sessions_for
from coldfix.primitives.measurement import MetricKind, metric_kind
from coldfix.primitives.registry import REGISTRY, Capability, ProjectProfile
from coldfix.primitives.scaling import Distribution
from coldfix.repair.falsification import CostClaim
from coldfix.sandbox.modes import ExecutionMode, Workbench
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import ResetMechanism
from coldfix.sandbox.verification import choose_reset
from coldfix.screening.binding import binder_for
from coldfix.state.persistent import PersistentStore

if TYPE_CHECKING:
    from coldfix.llm.client import ModelClient


class AssemblyError(Exception):
    """A campaign's resources could not be assembled."""


@contextmanager
def campaign_for(  # noqa: PLR0913 - twenty-five fields from nine owners, and the
    # signature is the honest form of that. A config object bundling them would be
    # a type whose only purpose is to be unpacked here, which `CLAUDE.md` refuses
    # until a second caller exists. S-17.18 added the bundling at the *edge* —
    # `cli/config.py` reads a file — which is a different thing: the caller there
    # is a file, and a file needs a shape.
    *,
    framework: str,
    reset_candidates: Sequence[ResetMechanism],
    capabilities: AbstractSet[Capability],
    counters: Sequence[str],
    workload: Callable[[], object],
    client: ModelClient,
    project: str,
    trust_key: str,
    revision: str,
    root: Path,
    python: Sequence[str],
    database_url: str,
    workbench: Workbench,
    store: PersistentStore,
    plan: Plan,
    entity: str,
    path: str,
    model: str,
    settings: str,
    source: str,
    suite_command: Sequence[str],
    metric: str,
    tokens: Tokens,
    claim: CostClaim,
    rate: ExchangeRate,
    ceiling_eur: Decimal | None = None,
    ledger: Ledger | None = None,
    shape: Distribution = Distribution.UNIFORM,
    alternatives: Sequence[Distribution] = (),
    facts: Mapping[object, bool] | None = None,
) -> Iterator[Resources]:
    """Everything one run needs, assembled and torn down.

    Raises:
        ProductionGuardError: `database_url` names a production database, or is
            one this guard cannot parse. Raised **before** anything opens, which
            is the only ordering that makes the refusal worth having.
        NoReliableResetError: no candidate reset restored the subject across its
            cycles. A run cannot proceed without one — every scale point after
            the first would be measured on top of the one before it.
        AssemblyError: the adapter offers no reset candidate at all.
    """
    database = VerifiedDatabase(database_url)

    # **One ledger under both.** `Budget.spent_eur` reads its own ledger's total,
    # so a budget and the sessions billing into a different one would each see a
    # fraction of the spend and the run could pass six ceilings on the way to
    # breaching one. S-17.4's argument, one layer up.
    shared = ledger if ledger is not None else Ledger()
    budget = Budget(ledger=shared, rate=rate, ceiling_eur=ceiling_eur)

    candidates = list(reset_candidates)
    if not candidates:
        message = (
            f"{framework} offered no way to reset {root}, so nothing can be "
            "measured twice. Every scale point after the first would be taken on top of the "
            "one before it, and the growth that showed would be arithmetic rather than a defect"
        )
        raise AssemblyError(message)

    with workbench.open(revision, mode=ExecutionMode.DIAGNOSTIC) as diagnostic:
        surface = SessionSurface(diagnostic)

        # **Chosen against the live subject, not supplied.** S-2.7 verifies a
        # reset by driving it ten times and comparing fingerprints, and the
        # workload it drives has to be this subject's — one verified against
        # something else would restore a database this run never touches.
        reset = choose_reset(candidates, database, workload)

        # `counters` are the adapter's declared hook names, supplied rather than
        # read: the catalogue decides which counters exist and the adapter says
        # which of them it supplies, and a campaign naming its own would ask for a
        # hook nothing registers.
        declared = tuple(sorted(counters))
        yield Resources(
            workbench=workbench,
            sessions=sessions_for(
                playbook=project, source=source, rate=rate, ceiling_eur=ceiling_eur, ledger=shared
            ),
            client=client,
            budget=budget,
            store=store,
            project=project,
            trust_key=trust_key,
            revision=revision,
            root=root,
            python=python,
            ground=grounder_for(
                root,
                python=python,
                surface=surface,
                plan=plan,
                reset=reset,
                settings=settings,
            ),
            hands=hands_on(surface),
            bind=binder_for(root, python=python, surface=surface, reset=reset),  # type: ignore[arg-type]
            measure=measurer_for(  # type: ignore[arg-type]
                diagnostic=diagnostic,
                python=python,
                path=path,
                entity=entity,
                metrics=Metrics(
                    cost=metric,
                    kinds=_kinds_of(metric, declared),
                    calls=_calls_of(declared),
                ),
                claim=claim,
                shape=shape,
                alternatives=alternatives,
            ),
            instruments=REGISTRY.select(
                ProjectProfile(
                    # **The adapter's capabilities and the harness's, together.**
                    # S-14.1 derives the second as the complement of the first, so
                    # a capability in neither is one every primitive requiring it
                    # is withheld for — which is the honest report rather than a
                    # silent gap.
                    capabilities=frozenset(capabilities),
                    facts=dict(facts or {}),  # type: ignore[arg-type]
                )
            ),
            executor=executor_for(
                REGISTRY.select(
                    ProjectProfile(
                        capabilities=frozenset(capabilities),
                        facts=dict(facts or {}),  # type: ignore[arg-type]
                    )
                ),
                {"invoke": lambda: None, "reset": reset, "seed": lambda scale: None},
            ),
            probe=probe_for(plan.workload_id, path=path, model=model, settings=settings),
            source=source,
            suite_command=suite_command,
            metric=metric,
            counters=declared,
            tokens=tokens,
        )


_TIMEOUT = 300.0

_RESET_SCALE = 1
"""What `choose_reset` drives the workload at. One row is enough to make the
subject write something a reset has to undo, and S-2.7 drives it ten times."""


def _kinds_of(metric: str, counters: Sequence[str]) -> dict[str, MetricKind]:
    """What each measured number is made of. **Derived here, and only here.**

    `Metrics` refuses an empty mapping — *no rule for what a move in them means, a
    count is exact and a duration is one sample, and the two disagree about every
    small move* — so something has to fill it, and this composer is what found
    that nothing did.

    **`metric_kind` is used because these are catalogue names, and that is the
    whole of the argument.** S-8.12 forbids reading a kind off a *spelling*, and
    it is right: `metric_kind` defaults to `COUNT` and the thesis ablation reports
    `seconds.share_removed`, a share of a duration it would call a count. But the
    names here are not derived — they are the cost metric the campaign was given
    and the hooks the adapter declared, every one of which `primitives.counters`
    names and `register_counter` refuses to invent. The catalogue is the authority
    on those, and a campaign asking the model instead would be asking about a
    fact.

    A metric produced *by* a primitive still travels as the primitive's own
    `kinds`, through `Measured`. Nothing here touches that path.
    """
    return {name: metric_kind(name) for name in {metric, *counters}}


def _calls_of(counters: Sequence[str]) -> str | None:
    """A call count, if the adapter declared one.

    `None` is the honest answer where it did not, and `Metrics` says so: every
    `None` there becomes an `UNTESTED` class rather than a check that quietly
    passes.
    """
    return next((name for name in counters if name.endswith("query")), None)
