"""What each node actually does, bound to the epics that do it.

Epic 12, S-12.7 — the open half of S-12.1's AC 3. That story wired seven nodes,
four routers and a compiled graph, and left *runs end to end on the target repo*
undone because `Wiring` takes seven `Step`s and nothing built them.

**The graph owns the shape, the epics own the work, and this owns the
translation.** There is exactly one thing between a node and an epic's entry
point, and it is a change of representation: `CheckpointedState` is JSON because
ADR 003 puts it in SQLite, and every entry point takes live objects. So a node is
a *rehydrate, call, serialize* sandwich, and the two slices are where a run loses
things.

**Two kinds of session, and conflating them is the mistake this module was
written wrong once to find.** Every compose entry point takes a
`cost.session.Session` **positionally** — the prompt, the budget, the cached
prefix — and takes `sandbox.modes.DiagnosticSession` or `CandidateSession` by
*keyword*, which is a worktree bound to a container. They share a name and
nothing else. `Sessions` builds the first, keyed by the step's system prompt
because that is what `refuse_shared_session` compares; `Workbench` opens the
second.

**What is supplied and why.** Everything in `Resources` is something a resumed
run must be handed again, because none of it survives JSON — that is the whole
reason the checkpointed/live split exists. The list is long, and its length is
the finding rather than a smell: `graph.py` records that the seven entry points
want eleven kinds of argument between them, and this is that inventory written
down once instead of threaded through the graph.

**`ship` does F14 and nothing else.** The pull request is S-16.2, two epics away.
What exists is the half the graph reads: `08-audit.md` F14's per-workload
invalidation, computed by `state.staleness.screening_plan` and consumed by
`graph.after_ship`. A stub PR here would be a second, worse answer to a question
another epic owns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from coldfix.audit import invocation, patchaudit, testquality
from coldfix.audit.compose import audit_finding as compose_audit_finding
from coldfix.audit.equivalence import Probe
from coldfix.audit.patchcompose import Measurements, Subject
from coldfix.audit.patchcompose import audit_patch as compose_audit_patch
from coldfix.audit.verdict import Route as FindingRoute
from coldfix.bench.stats import Growth
from coldfix.cost.budget import Budget
from coldfix.cost.session import Session
from coldfix.diagnosis import explain, hypothesis
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.diagnosis.compose import chain_of
from coldfix.diagnosis.emit import conditions_for
from coldfix.diagnosis.log import Experiment, ExperimentLog
from coldfix.diagnosis.loop import Executor, run_investigation
from coldfix.explorer import proposal
from coldfix.explorer.compose import Grounded
from coldfix.explorer.loop import Hands, explore
from coldfix.llm.client import ModelClient
from coldfix.orchestrator.graph import Step, Wiring, decided, null_result
from coldfix.primitives.registry import Selection
from coldfix.repair import falsification, testaudit
from coldfix.repair import patch as patch_module
from coldfix.repair.compose import Repaired, gate_and_audit
from coldfix.repair.compose import repair as compose_repair
from coldfix.repair.falsification import FalsificationTest
from coldfix.repair.memory import recall, record_all
from coldfix.repair.mustfail import Falsified
from coldfix.repair.patch import Patch
from coldfix.repair.slack import REVIEWED_AT_EVERY_LEVEL
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession, ExecutionMode, Workbench
from coldfix.sandbox.patching import touched_paths
from coldfix.screening.growth import screen as screen_workloads
from coldfix.screening.workload import BoundWorkload, Workload
from coldfix.state.checkpoint import CheckpointedState
from coldfix.state.persistent import PersistentStore
from coldfix.state.staleness import Coverage, ScreeningAction, screening_plan
from coldfix.state.staleness import Patch as StalePatch
from coldfix.state.trust import Outcome, record_outcome

_EXPLORER_PROMPT = proposal._SYSTEM
_INVESTIGATION_PROMPT = hypothesis._SYSTEM
_EXPLANATION_PROMPT = explain._SYSTEM
_FINDING_AUDIT_PROMPT = invocation._SYSTEM
_SURGEON_TEST_PROMPT = falsification._SYSTEM
_SURGEON_PATCH_PROMPT = patch_module._SYSTEM
_TEST_AUDIT_PROMPT = testaudit.SYSTEM
_PATCH_AUDIT_PROMPT = patchaudit.SYSTEM
_TEST_QUALITY_PROMPT = testquality.SYSTEM
"""Which prompt each session's cached prefix must carry.

**Every one is a real prompt owned by the role making the call**, which
`agents/roles.py` asserts independently — so a session built here cannot belong
to an agent that is not the one spending. The four audit prompts are load-bearing
rather than descriptive: `refuse_shared_session` compares `session.system`
against them and refuses a session whose prefix belongs to somebody else, because
*the isolation is the fresh message list **and** the fresh prompt*, and a shared
session undoes the second silently.

The three that are not checked are named the same way anyway. A session's prefix
is what `04-cost.md` §4 caches, so one carrying a prompt no step uses would be
paying for a cache nothing hits.
"""

_SUSPICIOUS = frozenset({Growth.SUPERLINEAR})
"""Growth worth an investigation.

One member, because S-1.5's vocabulary has three classes and only one of them
is a finding. **Written as a set rather than an equality** so that a fourth
class — the sublinear one the docstring on `Growth` records as deliberately
absent — has somewhere to go if it is ever added, without this line becoming
the place that quietly decides it is uninteresting."""


class AdapterError(Exception):
    """A node could not do its work with the state it was given."""


class MissingInputError(AdapterError):
    """A node needs a channel that nothing wrote.

    Raised rather than defaulted, and it is the argument `_decision` makes in
    `graph.py`. A repair node handed no chain could invent an empty one and give
    the Surgeon nothing to work from; what reaches a human then is a failed
    repair rather than the truth, which is that the investigation never ran.
    """


class Sessions(Protocol):
    """One cost session per agent step, keyed by that step's system prompt.

    **Per step, not per agent.** `refuse_shared_session` rejects a session whose
    cached prefix belongs to another prompt, which is what stops a hypothesis
    being justified by the framing that produced it — so a factory that returned
    one session for the whole run would defeat the boundary rather than serve it.

    Supplied because the playbook, the source, the exchange rate and the phase's
    stall threshold are the caller's facts, and a module that chose them would be
    choosing a budget.
    """

    def __call__(self, system: str) -> Session: ...


class Grounder(Protocol):
    """Run S-7.13's mechanical sequence. `ground_workload`, bound to a repo.

    **No longer the whole of grounding**, and the narrowing is S-7.14's. The
    sequence establishes three of the nine stage predicates and cannot establish
    the other six; the loop above it repairs those, and it is the loop the node
    calls. What is supplied here is what a bound sequence always was — the
    repository's own facts: its interpreter, how to make a request of it, the plan
    the Explorer decided, and the reset proof.
    """

    def __call__(self) -> Grounded: ...


class Binder(Protocol):
    """Turn the workload artifacts in state back into runnable bindings.

    `Workload` is the artifact and survives a checkpoint; `BoundWorkload` carries
    the callables that seed, invoke and reset it, and cannot. So the state keeps
    the first and this reproduces the second — the whole reason screening cannot
    read its input straight out of the channel it was written to.
    """

    def __call__(self, workloads: Sequence[Workload]) -> Sequence[BoundWorkload]: ...


class Measurer(Protocol):
    """Before-and-after for one patch. **The shape the orchestrator has to fill.**

    `Measurements`' own docstring says neither attack that reasons over numbers
    measures anything itself, because `CLAUDE.md` puts measuring in the harness.
    This is where the harness's answer arrives, and it is supplied for the same
    reason: an adapter that computed it would be an agent reporting a measurement.
    """

    def __call__(self, patch: Patch, *, candidate: CandidateSession) -> Measurements: ...


@dataclass(frozen=True)
class Tokens:
    """The two measured counts every agent entry point takes.

    Named rather than passed as a pair of bare integers, because they are two
    different numbers of the same type and transposing them is invisible: one is
    the cached prefix and one is the whole prompt, and a caller that swaps them
    gets a working run with wrong accounting.
    """

    prefix: int
    prompt: int


@dataclass(frozen=True)
class Resources:
    """The live objects no checkpoint can hold."""

    workbench: Workbench
    sessions: Sessions
    client: ModelClient
    budget: Budget
    store: PersistentStore
    """The journal, and **all three of its collections**. Required, not optional.

    Renamed from `failures` at S-13.6, because by then it was one store answering
    three questions: what was tried for a finding and did not work (S-13.3), what
    has been learned about projects of a kind (S-13.1), and what autonomy this
    project has earned (S-13.4). A field named for one of them is a field the
    next caller looks past.

    An optional store would be a switch that turns those guarantees off with
    nothing to justify it — S-12.4's argument about the ship gate, and the reason
    a run without it repeats itself after a rewind."""

    project: str
    """Which project this campaign is about. **The ledger's unit.**

    A level is *this project's own history*, and F15's whole finding is that
    trust learned elsewhere is context rather than authority — so an outcome
    recorded without a project would be somebody else's history counted as this
    one's."""

    trust_key: str
    """What this campaign's outcomes are filed under: `ledger_key(category,
    shape)`.

    **Supplied, because neither half is derivable here.** The fix category is a
    string nothing enumerates (S-13.4), and the shape is measured from the
    workload's own observations — which the caller has and this module would have
    to reach up through two packages to obtain."""

    revision: str
    """The commit every session opens against. One value, so a diagnostic and a
    candidate session cannot silently be measuring two different trees."""

    root: Path
    """The checkout the Explorer stands up. Supplied rather than derived from
    `revision`, because a revision names a commit and the Explorer needs a
    directory the stage predicates can be measured against."""

    python: Sequence[str]
    """The subject's interpreter, as a command. S-7.2's convention: nothing under
    `src/` chooses one on its own account."""

    ground: Grounder
    hands: Hands
    """How a command the Explorer proposes actually gets run.

    Supplied for the same reason `executor` is: `03-agents.md` §2.5 puts the
    denylist, the blocked egress and the workspace confinement on the container
    the command runs in, and a loop holding its own `execute` would be a second
    place all three have to exist."""

    bind: Binder
    measure: Measurer
    instruments: Selection
    executor: Executor
    probe: Probe
    source: str
    suite_command: Sequence[str]
    metric: str
    """Which of the workload's measurements the symptom quotes. Required for
    `symptom_for`'s reason: a symptom quoting a metric nobody measured is the
    first non-negotiable broken at the top of the report."""

    tokens: Tokens
    coverages: Sequence[Coverage] = ()
    """Which files each workload ran, for F14. Empty means nothing is known, and
    `Coverage` treats that as the absence of a claim rather than as *touches
    nothing* — so an unknown workload is re-screened rather than trusted."""


# ---------------------------------------------------------------- the seven nodes


def ground(resources: Resources, state: CheckpointedState) -> Mapping[str, object]:
    """Run the Explorer. Writes `project` and `workloads`.

    Reads nothing from `state`: grounding is the first node and there is nothing
    yet to read. It takes the argument because every `Step` does.

    **The session is built here rather than inside the loop**, and that is what
    puts the Explorer inside the boundary the other four agents are already
    behind: `Sessions` is keyed on the step's system prompt because that is what
    `refuse_shared_session` compares, so a loop that made its own session would
    be the one agent whose prefix nobody checked.

    **A repository that will not ground is a null result, not an exception.**
    S-7.11's acceptance is that the Explorer reports failure on a fourth
    repository rather than claiming success on empty data, and `00-BRIEF.md` §9
    ships that as an answer — so the failure report reaches the channel a person
    reads instead of unwinding the graph.
    """
    del state
    exploration = explore(
        resources.sessions(_EXPLORER_PROMPT),
        resources.client,
        root=resources.root,
        python=resources.python,
        ground=resources.ground,
        hands=resources.hands,
        measured_prefix_tokens=resources.tokens.prefix,
        measured_prompt_tokens=resources.tokens.prompt,
    )
    if exploration.grounded is None:
        return null_result(exploration.report())

    return {
        "project": dict(exploration.grounded.facts()),
        "workloads": [exploration.grounded.workload.model_dump(mode="json")],
        # **S-13.5's curve reads this and had nothing to read before.** Steps to
        # first runnable workload is the learning-curve axis, and while grounding
        # was nine mechanical stages run once each it was the same number for
        # every repository in the world.
        "flags": [{"grounding_steps": exploration.steps}],
    }


def screen(resources: Resources, state: CheckpointedState) -> Mapping[str, object]:
    """Measure every workload against volume. Writes `screening`, **keyed by id**.

    The key is `Workload.id` because that is what F14 filters on and what S-6.3
    changed the channel's shape for — a screening pass whose entries could not be
    attributed to a workload gives `after_ship` a correct answer with nowhere to
    put it.

    A workload flagged here is one whose growth is superlinear on some metric.
    Nothing flagged ends the run as a null result, which `00-BRIEF.md` §9 makes an
    answer rather than a failure.
    """
    workloads = _workloads(state)
    if not workloads:
        return null_result("grounding produced no workloads, so there was nothing to screen")

    screened = screen_workloads(resources.bind(workloads))
    return {
        "screening": {
            item.workload.id: {
                "growth": {
                    name: growth.fit.growth.name
                    for name, growth in item.growth.items()
                    if growth.fit.growth is not None
                },
                "flagged": _superlinear(item.growth),
            }
            for item in screened
        }
    }


def investigate(resources: Resources, state: CheckpointedState) -> Mapping[str, object]:
    """Run experiments until something is confirmed. Writes `target`, `chain`,
    and the experiment log.

    **Two steps in one node, and they are one phase.** `run_investigation` runs
    until a cause is confirmed or the budget stops it; `chain_of` states what that
    adds up to. Splitting them across nodes would put a checkpoint between an
    investigation and its own conclusion, and a resumed run would have to
    reconstruct a live `Investigation` from JSON to finish the sentence.

    **Stopping is not failing.** An investigation that ran out writes no chain and
    the finding audit is never reached; `00-BRIEF.md` §9 ships that as an answer.
    """
    target = _first_flagged(state)
    if target is None:
        return null_result("screening flagged nothing, so there was nothing to investigate")

    workload = _workload_named(state, target)
    session = resources.sessions(_INVESTIGATION_PROMPT)
    investigation = run_investigation(
        session,
        resources.client,
        instruments=resources.instruments,
        source=resources.source,
        conditions=conditions_for(workload),
        execute=resources.executor,
        measured_prefix_tokens=resources.tokens.prefix,
        measured_prompt_tokens=resources.tokens.prompt,
        finding_id=target,
    )

    written = [_stored(item) for item in investigation.log.experiments]
    if investigation.stopped is not None:
        return {
            "target": target,
            "chain": None,
            "experiments": written,
            "flags": [{"stopped": investigation.stopped.value}],
        }

    chain = chain_of(
        resources.sessions(_EXPLANATION_PROMPT),
        resources.client,
        investigation=investigation,
        workload=workload,
        metric=resources.metric,
        complexity=_complexity(state, target),
        source=resources.source,
        measured_prefix_tokens=resources.tokens.prefix,
        measured_prompt_tokens=resources.tokens.prompt,
        finding_id=target,
    )
    return {"target": target, "chain": chain.model_dump(mode="json"), "experiments": written}


def audit_finding(resources: Resources, state: CheckpointedState) -> Mapping[str, object]:
    """Attack the diagnosis. Writes `verdict` and the route S-9.8 decided.

    **The route is read, never re-derived.** S-9.8's `route` takes a `Budget`
    whose caps cannot be reconstructed from the state's projection of it, so the
    decision is made here — where the budget is — and written to the channel the
    edge reads.
    """
    target = str(_require(state.target, "target", "audit a finding"))
    workload = _workload_named(state, target)

    routing, _calls = compose_audit_finding(
        resources.sessions(_FINDING_AUDIT_PROMPT),
        resources.client,
        workload=workload,
        conditions=conditions_for(workload),
        log=_log_of(state),
        exclusions=(),
        measured_prefix_tokens=resources.tokens.prefix,
        measured_prompt_tokens=resources.tokens.prompt,
        finding_id=target,
    )

    # **The reasoning is recorded, not just the answer.** S-12.5 puts a human
    # here before any repair budget is spent, and *what was found and why* is
    # exactly what `Routing.describe` says — the verdict, where it sends the run,
    # and why that rather than the obvious. Writing only the verdict name would
    # leave a person deciding whether to spend three attempts on a finding they
    # can see the label of and not the argument for.
    return {
        "verdict": routing.verdict.verdict.name,
        "flags": [
            {
                "finding_audit": routing.describe(),
                "subject": routing.verdict.subject.value,
                "spends_repair": routing.spends_repair,
            }
        ],
        **decided(routing.route),
    }


def repair(resources: Resources, state: CheckpointedState) -> Mapping[str, object]:
    """Write a failing test, then patches until one passes it. Writes `repaired`.

    **Both halves in one node, because they are one phase.** `gate_and_audit`
    writes the falsification test, proves it fails unpatched, lets the Adversary
    attack it and re-runs the replacement; `repair` then writes patches against
    the test that survived. Passing the pre-audit test would verify a patch
    against a test the Adversary said a cheat could pass — S-10.3's *re-gated, not
    trusted* — and splitting the two across nodes would put a checkpoint between
    them where a resumed run could pick up the wrong one.

    An escalation writes no `repaired`, so there is nothing for `audit_patch` to
    audit and the run ends where a person picks it up.
    """
    chain = _chain_of_state(state)
    finding = str(_require(state.target, "target", "repair a finding"))

    with (
        resources.workbench.open(resources.revision, mode=ExecutionMode.DIAGNOSTIC) as diagnostic,
        resources.workbench.open(resources.revision, mode=ExecutionMode.CANDIDATE) as candidate,
    ):
        gated = gate_and_audit(
            resources.sessions(_SURGEON_TEST_PROMPT),
            resources.sessions(_TEST_AUDIT_PROMPT),
            resources.client,
            chain=chain,
            diagnostic=_diagnostic(diagnostic),
            measured_prefix_tokens=resources.tokens.prefix,
            measured_prompt_tokens=resources.tokens.prompt,
            finding_id=finding,
        )
        if not isinstance(gated, tuple):
            return {
                "repaired": None,
                "attempts": [{"gate": "not falsified", "reason": gated.reason.name}],
                **decided(FindingRoute.ESCALATE),
            }

        falsified, _audit = gated
        outcome = compose_repair(
            resources.sessions(_SURGEON_PATCH_PROMPT),
            resources.sessions(_TEST_AUDIT_PROMPT),
            resources.client,
            chain=chain,
            falsified=falsified,
            candidate=_candidate(candidate),
            measured_prefix_tokens=resources.tokens.prefix,
            measured_prompt_tokens=resources.tokens.prompt,
            # **The half a rewind cannot reach.** S-12.6 added this parameter and
            # left it empty, so a rewound run repeated the approach that had
            # already failed — F5's defect, held open on purpose until S-13.3
            # gave it a source. `attempts` is checkpointed and is therefore
            # exactly what a rewind discards; this is not.
            remembered=recall(resources.store, finding),
            finding_id=finding,
        )

    if not isinstance(outcome, Repaired):
        record_all(resources.store, finding, outcome.attempts)
        return {
            "repaired": None,
            "attempts": [{"escalated": outcome.report()}],
            **decided(FindingRoute.ESCALATE),
        }

    # **Recorded before the patch is handed on, and including the one that
    # worked.** S-11.7 can send it back after the Adversary breaks it, and an
    # approach that passed its own test and failed the audit is exactly what the
    # next attempt must not re-propose. Writing only on escalation would forget
    # precisely the attempts that got furthest.
    record_all(resources.store, finding, outcome.attempts)
    return {
        "repaired": _repaired(outcome, falsified),
        "attempts": [{"attempt": len(outcome.attempts), "patch": outcome.patch.describe()}],
    }


def audit_patch(resources: Resources, state: CheckpointedState) -> Mapping[str, object]:
    """Mount five attacks on the patch. Writes the route S-11.7 decided.

    **Authorize, attack, record, then route** is `audit_patch`'s own order and the
    defect Epic 11's composition check found; nothing here reorders it. What this
    adds is the two things the state cannot carry — the subject read off the
    candidate worktree and the measurements the harness took.
    """
    patch, falsified = _repaired_from(_require(state.repaired, "repaired", "audit a patch"))
    chain = _chain_of_state(state)
    finding = str(_require(state.target, "target", "audit a patch"))

    with (
        resources.workbench.open(resources.revision, mode=ExecutionMode.DIAGNOSTIC) as diagnostic,
        resources.workbench.open(resources.revision, mode=ExecutionMode.CANDIDATE) as candidate,
    ):
        # **Measured once and used twice.** The audit reasons over these numbers
        # and S-12.4's gate shows them to a person, and measuring again for the
        # second reader would put two different sets of figures under one patch —
        # the run is not deterministic enough for them to agree, and a human
        # comparing the report against the verdict would be right to distrust
        # both. It would also pay for the sweep twice.
        measured = resources.measure(patch, candidate=_candidate(candidate))
        audited = compose_audit_patch(
            resources.sessions(_PATCH_AUDIT_PROMPT),
            resources.sessions(_TEST_QUALITY_PROMPT),
            resources.client,
            patch=patch,
            test=falsified.test,
            chain=chain,
            falsified=falsified,
            subject=Subject.of(
                patch.diff,
                diagnostic=_diagnostic(diagnostic),
                candidate=_candidate(candidate),
                suite_command=resources.suite_command,
                probe=resources.probe,
            ),
            measurements=measured,
            budget=resources.budget,
            measured_prefix_tokens=resources.tokens.prefix,
            measured_prompt_tokens=resources.tokens.prompt,
            finding_id=finding,
        )

    return {
        "flags": [
            {
                "patch_audit": audited.routing.describe(),
                "verdict": audited.verdict.describe(),
                "before": dict(measured.domain_before),
                "after": dict(measured.domain_after),
            }
        ],
        **decided(audited.routing.route),
    }


def ship(resources: Resources, state: CheckpointedState) -> Mapping[str, object]:
    """Invalidate what the patch made stale, and hand the patch to a human.

    **This is F14 and nothing else.** *After `ship` the graph returns to `screen`,
    but the code has changed and every prior screening measurement is now stale —
    re-screen only the workloads whose files the patch touched.* `screening_plan`
    decides that per workload and this applies it, so `after_ship` sees exactly
    the workloads that still need looking at.

    **It does not emit a pull request**, and the omission is deliberate rather
    than pending: S-16.2 owns the PR body — before and after on every varied axis,
    the evidence chain, the guard metrics, the Adversary verdict — and a stub here
    would be a second, worse answer to a question two epics away.
    """
    handover = _require(state.repaired, "repaired", "ship a patch")
    patch, _falsified = _repaired_from(handover)
    finding = str(_require(state.target, "target", "ship a patch"))

    # **§4 holds at any trust level, and this is where that is true rather than
    # intended.** S-13.6 lets a project's ledger level compile the ship gate away,
    # and a compile-time decision cannot see a patch that does not exist yet — so
    # a `slack-reducing` one is refused *here*, after it exists, whatever
    # `gates_for` returned. S-10.6 blocks auto-approval permanently; a level is
    # not a thing that clears it.
    if isinstance(handover, Mapping) and handover.get("slack_reducing"):
        return {
            "flags": [{"withheld": patch.describe(), "because": REVIEWED_AT_EVERY_LEVEL}],
            **decided(FindingRoute.ESCALATE),
        }

    # **The diff is what says which files moved, not a field beside it.** `Patch`
    # deliberately has no `files`: the agent would be restating what the diff
    # already contains, and a list that disagreed with its own diff is a scope
    # check passing against a claim rather than against the change.
    plan = screening_plan(resources.coverages, StalePatch.of(finding, touched_paths(patch.diff)))
    surviving = {
        name: entry
        for name, entry in (state.screening or {}).items()
        if plan.get(name, ScreeningAction.SCREEN_AGAIN) is ScreeningAction.KEEP
    }

    # **A shipped patch is a clean outcome, and the ledger learns from it.**
    # S-13.4 built the levels and nothing moved them; without this the gate at
    # `gates_for` can only ever read `GATED`, which is a ledger that exists and
    # is not written — as useless as one that exists and is not read.
    record_outcome(
        resources.store, resources.trust_key, project=resources.project, outcome=Outcome.ACCEPTED
    )

    return {
        "screening": surviving,
        "repaired": None,
        "target": None,
        "flags": [{"shipped": patch.describe(), "awaiting": "human review"}],
    }


def bind(resources: Resources) -> Wiring:
    """The seven steps, each closed over the same resources. **AC 3's remainder.**

    Closures rather than a class per node, because a node is a function of the
    state and one bundle of live objects, and seven classes holding one field each
    would be seven places for that field to be named differently.
    """
    return Wiring(
        ground=_step(ground, resources),
        screen=_step(screen, resources),
        investigate=_step(investigate, resources),
        audit_finding=_step(audit_finding, resources),
        repair=_step(repair, resources),
        audit_patch=_step(audit_patch, resources),
        ship=_step(ship, resources),
    )


def _step(
    adapter: Callable[[Resources, CheckpointedState], Mapping[str, object]], resources: Resources
) -> Step:
    """One adapter, bound to its resources, typed as the graph's `Step`.

    **Annotated `Step` rather than `Callable`, and S-6.3 predicted the mistake.**
    `Step.__call__` declares a *named* parameter, a `Callable`'s are
    positional-only, and a closure returned as `Callable[[CheckpointedState], ...]`
    fails at `add_node`. That error has now been made three times in this project
    — S-12.1 hit it, the first draft of this module hit it, and S-6.3 wrote it
    down before either existed.
    """

    def run(state: CheckpointedState) -> Mapping[str, object]:
        return adapter(resources, state)

    return run


# ---------------------------------------------------------------- rehydration


def _require(value: JsonValue | None, channel: str, doing: str) -> JsonValue:
    """Read a channel a node cannot work without, or say which one is empty."""
    if value is None:
        message = (
            f"nothing wrote {channel!r} and this node needs it to {doing}. A node that filled in "
            "a default here would carry on with a value no phase produced, and what reaches a "
            "human is then a failed phase rather than the truth, which is that an earlier one "
            "never ran"
        )
        raise MissingInputError(message)
    return value


def _workloads(state: CheckpointedState) -> tuple[Workload, ...]:
    return tuple(Workload.model_validate(item) for item in state.workloads)


def _workload_named(state: CheckpointedState, name: str) -> Workload:
    found = next((item for item in _workloads(state) if item.id == name), None)
    if found is None:
        known = sorted(item.id for item in _workloads(state))
        message = f"no workload named {name!r} in this state; it holds {known}"
        raise MissingInputError(message)
    return found


def _superlinear(growth: Mapping[str, object]) -> bool:
    """Whether any metric grew faster than the volume did.

    Read off the fit rather than judged here: S-1.5's vocabulary is what makes two
    screens comparable, and a threshold invented at this boundary would be a
    second opinion about what *suspicious* means.
    """
    return any(
        getattr(getattr(item, "fit", None), "growth", None) in _SUSPICIOUS
        for item in growth.values()
    )


def _first_flagged(state: CheckpointedState) -> str | None:
    """The workload to investigate next, in the order `flagged` reports them."""
    screening = state.screening or {}
    for name in sorted(screening):
        entry = screening[name]
        if isinstance(entry, Mapping) and entry.get("flagged"):
            return name
    return None


def _complexity(state: CheckpointedState, target: str) -> Mapping[str, Growth]:
    """The growth table screening measured, as the chain records it.

    Comes from the screening channel rather than from the investigation, because
    a chain's `complexity` is *measured growth per varying axis* and screening is
    what varied the axis.
    """
    entry = (state.screening or {}).get(target)
    if not isinstance(entry, Mapping):
        return {}
    table = entry.get("growth")
    if not isinstance(table, Mapping):
        return {}
    return {name: Growth[str(value)] for name, value in table.items()}


def _stored(experiment: Experiment) -> JsonValue:
    """One experiment, small enough to checkpoint.

    **`detail` is dropped and that is S-6.3's rule, not a shortcut.** That field
    is the full output — stdout, stacks, per-call timings — and S-8.4 holds it
    *always and renders it never*, because writing it into the log would
    invalidate the cached prefix. It would do worse to a checkpoint: forty
    experiments of raw output is the megabytes-per-node write F13 exists to
    prevent. What stays is the record a reader argues with, which is under a
    kilobyte and fits the same budget a reference would have.
    """
    return experiment.model_dump(mode="json", exclude={"detail"})


def _log_of(state: CheckpointedState) -> ExperimentLog:
    """Rebuild the experiment log from what the checkpoint kept.

    **Replayed through `append` rather than constructed**, because the log
    assigns the indices and `read_experiment(7)` has to mean the seventh
    experiment. Handing it a pre-built list would let a resumed run disagree with
    the one that wrote it about which experiment is which.
    """
    log = ExperimentLog()
    for item in state.experiments:
        record = Experiment.model_validate(item)
        log.append(
            hypothesis=record.hypothesis,
            primitive=record.primitive,
            rationale=record.rationale,
            target=record.target,
            design=record.design,
            measurement=record.measurement,
            verdict=record.verdict,
            outcome=record.outcome,
        )
    return log


def _chain_of_state(state: CheckpointedState) -> EvidenceChain:
    return EvidenceChain.model_validate(_require(state.chain, "chain", "work from a diagnosis"))


def _repaired(outcome: Repaired, falsified: Falsified) -> JsonValue:
    """The handover from `repair` to `audit_patch`, as JSON.

    Both halves, because the audit needs the patch *and* the proof its test failed
    on unpatched code — `Falsified`'s constructor refuses to describe a failure as
    a success, and re-deriving it on the other side of a checkpoint would be
    building that proof from something other than a run that actually failed.
    """
    return {
        "patch": outcome.patch.model_dump(mode="json"),
        "slack_reducing": outcome.needs_human_review,
        "falsified": {
            "test": falsified.test.model_dump(mode="json"),
            "evidence": falsified.evidence,
            "wall_seconds": falsified.wall_seconds,
        },
    }


def _repaired_from(payload: JsonValue) -> tuple[Patch, Falsified]:
    if not isinstance(payload, Mapping):
        message = f"the {'repaired'!r} channel holds {type(payload).__name__}, not a patch handover"
        raise MissingInputError(message)
    proof = payload["falsified"]
    if not isinstance(proof, Mapping):  # pragma: no cover - written by `_repaired` only
        message = "the patch handover carries no falsification proof"
        raise MissingInputError(message)
    return (
        Patch.model_validate(payload["patch"]),
        Falsified(
            test=FalsificationTest.model_validate(proof["test"]),
            evidence=str(proof["evidence"]),
            wall_seconds=float(str(proof["wall_seconds"])),
        ),
    )


def _diagnostic(session: DiagnosticSession | CandidateSession) -> DiagnosticSession:
    if not isinstance(session, DiagnosticSession):  # pragma: no cover - `open` selects on mode
        message = "expected a diagnostic session and the workbench opened a candidate one"
        raise AdapterError(message)
    return session


def _candidate(session: DiagnosticSession | CandidateSession) -> CandidateSession:
    if not isinstance(session, CandidateSession):  # pragma: no cover - as above
        message = "expected a candidate session and the workbench opened a diagnostic one"
        raise AdapterError(message)
    return session
