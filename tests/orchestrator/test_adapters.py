"""The translation between a checkpoint and an epic's entry point.

S-12.7. Every node here is a *rehydrate, call, serialize* sandwich, and the two
slices are what this module owns — the call in the middle belongs to an epic that
already has its own composition check. So these tests are about the slices: what
survives a round trip through JSON, what a node refuses to invent when a channel
is empty, and whether the seven closures actually satisfy the graph's `Step`.

**The full seven-node drive is not here and the backlog says why.** It needs a
container, a database and a recording per model call; S-17.1 owns that. What is
provable without them is that the translation does not lose anything, which is
where a run silently degrades.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from coldfix.agents.roles import owner_of
from coldfix.bench.stats import Growth
from coldfix.cost.accounting import Agent
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict
from coldfix.explorer import proposal
from coldfix.explorer.playbook import PlaybookEntry, as_entry, learned_from_auth
from coldfix.orchestrator import adapters
from coldfix.orchestrator.adapters import (
    MissingInputError,
    Resources,
    Tokens,
    _complexity,
    _first_flagged,
    _log_of,
    _repaired,
    _repaired_from,
    _require,
    _stored,
    _workload_named,
    bind,
)
from coldfix.orchestrator.graph import GraphError, Node, Wiring, assemble, order
from coldfix.primitives.counters import DB_QUERY
from coldfix.repair.compose import Repaired
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import Falsified
from coldfix.repair.patch import Attempt, Patch
from coldfix.repair.slack import Classification
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession, ExecutionMode
from coldfix.screening.workload import Workload
from coldfix.state.checkpoint import CheckpointedState
from coldfix.state.persistent import Collection
from coldfix.state.persistent import Entry as PersistentEntry
from fixtures.chains import an_evidence_chain
from fixtures.workloads import HELPDESK_TICKETS

DIFF = """\
--- a/shop/rendering.py
+++ b/shop/rendering.py
@@ -54,3 +54,3 @@
-    return [self.render(row) for row in rows]
+    return self.render_all(rows)
"""


def an_experiment(log: ExperimentLog, *, index_hint: str = "renderer") -> Experiment:
    return log.append(
        hypothesis=f"the {index_hint} owns the cost",
        primitive="ablation.stub",
        rationale="scaling came back flat, so the database is excluded",
        target="ExpensiveRenderer.render",
        design="ablation.stub(target='ExpensiveRenderer.render')",
        measurement={"seconds": 8.24, "seconds.share_removed": 0.87},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the renderer removed almost all of the wall time",
        detail="x" * 5000,
    )


# ==================================================== the experiment log round-trips


def test_the_log_survives_a_checkpoint_with_its_indices() -> None:
    """`read_experiment(7)` has to mean the seventh experiment, so a resumed run
    and the run that wrote it must agree about which experiment is which."""
    original = ExperimentLog()
    first = an_experiment(original, index_hint="renderer")
    second = an_experiment(original, index_hint="serializer")

    restored = _log_of(CheckpointedState(experiments=[_stored(first), _stored(second)]))

    assert [item.index for item in restored.experiments] == [1, 2]
    assert restored.experiment(1).hypothesis == first.hypothesis
    assert restored.experiment(2).hypothesis == second.hypothesis
    assert restored.experiment(1).measurement == first.measurement


def test_the_full_output_is_not_checkpointed() -> None:
    """**S-6.3's rule, applied.** `detail` is stdout, stacks and per-call timings;
    S-8.4 holds it always and renders it never, and forty of them is the
    megabytes-per-node write F13 exists to prevent."""
    log = ExperimentLog()
    experiment = an_experiment(log)
    assert len(experiment.detail) == 5000, "the fixture has something worth dropping"

    stored = _stored(experiment)

    assert isinstance(stored, dict)
    assert "detail" not in stored
    assert stored["measurement"] == dict(experiment.measurement), "the evidence stays"


def test_a_stored_experiment_stays_well_under_the_per_entry_budget() -> None:
    """S-6.3 budgets ~1 KiB per experiment so that forty fit in the checkpoint.
    Dropping `detail` is what makes the record cost about what a reference would."""
    log = ExperimentLog()

    assert len(json.dumps(_stored(an_experiment(log)))) < 1024


# ==================================================== the patch handover round-trips


def a_patch() -> Patch:
    return Patch(diff=DIFF, approach="render every row in one pass", rationale="the sweep says so")


def a_falsified() -> Falsified:
    return Falsified(
        test=FalsificationTest(
            claim="the list endpoint stops re-rendering the author for every book",
            script="raise AssertionError('still N+1')",
            equivalence="the same books in the same order",
            cost=CostClaim(
                metric="seconds",
                baseline=8.24,
                at_most=2.0,
                guards=(Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
            ),
            catches=(Cheat.CACHED_STATE, Cheat.STUBBED_RESPONSE),
        ),
        evidence="1 failed in 0.4s",
        wall_seconds=0.4,
    )


def test_the_patch_and_its_proof_survive_the_checkpoint_between_the_two_nodes() -> None:
    """**The channel §1.1 never had.** `repair` produces this and `audit_patch`
    consumes it, with a checkpoint in between and nothing to carry it across until
    S-12.7 added `repaired`."""
    patch, falsified = _repaired_from(_repaired_payload())

    assert patch.diff == DIFF
    assert patch.approach == a_patch().approach
    assert falsified.evidence == "1 failed in 0.4s"
    assert falsified.test.claim == a_falsified().test.claim


def test_the_proof_of_failure_is_carried_rather_than_rebuilt() -> None:
    """`Falsified` refuses to describe a failure as a success — its constructor
    needs the evidence of a run that actually failed. Re-deriving it on the far
    side of a checkpoint would be building that proof from something other than
    the run, so the evidence travels."""
    _patch, falsified = _repaired_from(_repaired_payload())

    with pytest.raises(Exception, match="assertion that the test failed"):
        Falsified(test=falsified.test, evidence="   ", wall_seconds=falsified.wall_seconds)


def _repaired_payload() -> JsonValue:
    outcome = Repaired(
        patch=a_patch(),
        classification=Classification(removals=()),
        attempts=(),
    )
    return _repaired(outcome, a_falsified())


# ==================================================== nothing is invented


@pytest.mark.parametrize("channel", ["chain", "repaired", "target"])
def test_a_node_refuses_a_channel_nothing_wrote(channel: str) -> None:
    """**Raised rather than defaulted**, which is `_decision`'s argument in
    `graph.py`. A node that filled in a default carries on with a value no phase
    produced, and what reaches a human is a failed phase rather than the truth —
    that an earlier one never ran."""
    with pytest.raises(MissingInputError, match=re.escape(f"nothing wrote {channel!r}")):
        _require(None, channel, "do its work")


def test_a_workload_the_state_does_not_hold_is_named_rather_than_guessed() -> None:
    state = CheckpointedState(workloads=[])

    with pytest.raises(MissingInputError, match=re.escape("no workload named 'shop.books'")):
        _workload_named(state, "shop.books")


# ==================================================== screening reads the fit


def test_the_node_holds_no_opinion_about_what_is_worth_investigating() -> None:
    """**This test used to assert the defect**, and its reasoning sounded right.

    It drove a local `_superlinear` helper and asserted that a `LINEAR` metric is
    not a finding, with the docstring *"S-1.5's vocabulary decides, not a
    threshold invented at this boundary"*. The vocabulary does decide — against
    each metric's own **expectation**, which is `screening/flagging.py`'s whole
    subject. A round-trip count is expected to be constant, so a linear one is a
    finding, and the helper cleared every N+1 this system exists to find.

    What is asserted now is the absence: this module holds no threshold and no
    suspicious-growth set. Epic 16's composition check drives the node itself and
    compares what it writes against `flag`.
    """
    source = inspect.getsource(adapters)
    body = source.replace("# `_SUSPICIOUS` lived here", "")

    assert "_SUSPICIOUS" not in body
    assert "conclude(" in source, "the judgement comes from Epic 4's own entry point"


def test_the_target_is_the_first_flagged_workload_in_the_ranked_order() -> None:
    """`order` is `Plan.investigate`'s position, and it is not the alphabet.

    Two runs of the same screen must investigate the same workload first —
    `00-BRIEF.md` §6 makes agreement across runs the headline metric — and the
    old key was the name, which is stable and wrong: `rank` puts growth flags
    ahead of flat-cost ones as a class, and reading the set back by name
    discarded that.
    """
    state = CheckpointedState(
        screening={
            "z.workload": {"flagged": True, "growth": {}, "order": 0},
            "a.workload": {"flagged": True, "growth": {}, "order": 1},
            "m.workload": {"flagged": False, "growth": {}, "order": None},
        }
    )

    assert _first_flagged(state) == "z.workload"


def test_an_older_checkpoint_without_an_order_still_picks_stably() -> None:
    """A resumed run must not crash on state written before this field existed,
    and two runs of it must still agree — so the name is the fallback key."""
    state = CheckpointedState(
        screening={
            "z.workload": {"flagged": True, "growth": {}},
            "a.workload": {"flagged": True, "growth": {}},
        }
    )

    assert _first_flagged(state) == "a.workload"


def test_nothing_flagged_is_a_null_result_rather_than_a_target() -> None:
    state = CheckpointedState(screening={"a": {"flagged": False, "growth": {}}})

    assert _first_flagged(state) is None


def test_the_growth_table_reaches_the_chain() -> None:
    """A chain's `complexity` is *measured growth per varying axis*, and screening
    is what varied the axis — so it comes from that channel, not from the
    investigation."""
    state = CheckpointedState(
        screening={"shop.books": {"flagged": True, "growth": {"rows": "SUPERLINEAR"}}}
    )

    assert _complexity(state, "shop.books") == {"rows": Growth.SUPERLINEAR}


def test_a_workload_screening_never_reached_contributes_no_growth() -> None:
    """Empty rather than invented: a chain quoting a growth class nobody measured
    is the first non-negotiable broken in the complexity table."""
    assert _complexity(CheckpointedState(), "shop.books") == {}


# ==================================================== the seven steps are steps


def test_every_node_the_graph_names_has_an_adapter() -> None:
    """**The check that catches an eighth node.** `Wiring` is a dataclass with
    seven fields; a node added to `Node` with no adapter behind it would compile,
    return an empty update, and pass the phase through silently."""
    for name in order():
        assert callable(getattr(adapters, name)), f"{name} has no adapter"
    assert set(order()) == {item.value for item in Node}


def unused() -> Any:
    """A live object no test here touches.

    `bind` closes over its resources and calls none of them, so the seven
    closures can be built — and the graph compiled — without a container, a
    database or a model. That is the point being tested: the wiring is separable
    from the work.
    """
    return cast(Any, object())


def test_the_bound_steps_are_what_the_graph_will_accept() -> None:
    """**The trap S-6.3 wrote down before either story that fell into it.**

    LangGraph's node protocol declares `__call__(self, state: ...)` with a *named*
    parameter, so a closure typed as `Callable[[CheckpointedState], ...]` — whose
    parameters are positional-only — fails at `add_node`. S-12.1 hit it, the first
    draft of the adapters hit it, and this compiles the real graph over the real
    closures so a third time fails here rather than in a run.
    """
    wiring = bind(
        Resources(
            workbench=unused(),
            sessions=unused(),
            client=unused(),
            budget=unused(),
            store=unused(),
            project="shop",
            trust_key="query-batching@django/postgres/1e2",
            revision="HEAD",
            root=Path(),
            python=["python"],
            ground=unused(),
            hands=unused(),
            bind=unused(),
            measure=unused(),
            instruments=unused(),
            executor=unused(),
            probe=unused(),
            source="shop/views.py::ListView.list_books",
            suite_command=["pytest"],
            metric="seconds",
            counters=[DB_QUERY],
            tokens=Tokens(prefix=8000, prompt=900),
        )
    )

    compiled = assemble(wiring, gated=False)

    registered = set(compiled.get_graph().nodes) - {"__start__", "__end__"}
    assert registered == {item.value for item in Node}


def test_a_wiring_missing_a_step_is_refused_rather_than_compiled() -> None:
    """A node with nothing behind it returns an empty update, so the run passes
    straight through the phase and the state simply never gains what it produces."""
    with pytest.raises(GraphError, match="no step supplied for"):
        assemble(Wiring(**{**{item.value: unused() for item in Node}, "ship": None}), gated=False)


@pytest.fixture
def chain_state() -> CheckpointedState:
    """A state parked where `repair` reads: a target and a chain behind it."""
    return CheckpointedState(
        target="shop.books.list",
        chain=an_evidence_chain().model_dump(mode="json"),
    )


# ==================================================== S-13.3 — the memory is consulted


class FakeDiagnostic(DiagnosticSession):
    """A diagnostic worktree that is opened and closed and does nothing else.

    **Subclassed rather than duck-typed**, because `_diagnostic` and `_candidate`
    check the type — the two sessions are the pair S-2.3 keeps apart, and an
    adapter that accepted either would be the place that separation is lost. The
    guards rejected a plain fake, which is them working.
    """

    def __init__(self) -> None:
        return None

    def __enter__(self) -> FakeDiagnostic:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeCandidate(CandidateSession):
    def __init__(self) -> None:
        return None

    def __enter__(self) -> FakeCandidate:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeWorkbench:
    def open(self, revision: str, *, mode: ExecutionMode) -> DiagnosticSession | CandidateSession:
        del revision
        return FakeDiagnostic() if mode is ExecutionMode.DIAGNOSTIC else FakeCandidate()


class FakeStore:
    """A failure memory that records what it was asked and told."""

    def __init__(self, holds: list[Attempt] | None = None) -> None:
        self.holds = holds or []
        self.asked: list[str] = []
        self.written: list[tuple[str, Attempt]] = []


def a_repair_attempt(approach: str = "prefetch") -> Attempt:
    return Attempt(
        patch=Patch(diff=DIFF, approach=approach, rationale="the sweep says so"),
        failure="still 1001 queries",
    )


def wire_repair(monkeypatch: pytest.MonkeyPatch, store: FakeStore) -> dict[str, Any]:
    """Replace the epic calls and the store, leaving the adapter's own wiring.

    **The point is what the adapter passes**, not what `repair` does with it —
    Epic 10 has its own composition check for that. So the two model-calling
    halves are recorded rather than run.
    """
    seen: dict[str, Any] = {}

    def fake_recall(_store: Any, finding: str) -> tuple[Attempt, ...]:
        store.asked.append(finding)
        return tuple(store.holds)

    def fake_record(_store: Any, finding: str, attempts: Sequence[Attempt]) -> int:
        store.written.extend((finding, item) for item in attempts)
        return len(attempts)

    monkeypatch.setattr(adapters, "recall", fake_recall)
    monkeypatch.setattr(adapters, "record_all", fake_record)
    monkeypatch.setattr(
        adapters,
        "gate_and_audit",
        lambda *a, **k: (a_falsified(), object()),
    )

    def fake_repair(*_a: Any, **kwargs: Any) -> Repaired:
        seen.update(kwargs)
        return Repaired(
            patch=a_patch(),
            classification=Classification(removals=()),
            attempts=(a_repair_attempt("first"), a_repair_attempt("second")),
        )

    monkeypatch.setattr(adapters, "compose_repair", fake_repair)
    return seen


def repair_resources(store: FakeStore) -> Resources:
    return Resources(
        workbench=cast(Any, FakeWorkbench()),
        sessions=lambda system: cast(Any, object()),
        client=unused(),
        budget=unused(),
        store=cast(Any, store),
        project="shop",
        trust_key="query-batching@django/postgres/1e2",
        revision="HEAD",
        root=Path(),
        python=["python"],
        ground=unused(),
        hands=unused(),
        bind=unused(),
        measure=unused(),
        instruments=unused(),
        executor=unused(),
        probe=unused(),
        source="shop/views.py",
        suite_command=["pytest"],
        metric="seconds",
        counters=[DB_QUERY],
        tokens=Tokens(prefix=8000, prompt=900),
    )


def test_the_repair_node_hands_the_surgeon_what_the_store_remembers(
    monkeypatch: pytest.MonkeyPatch, chain_state: CheckpointedState
) -> None:
    """**The join S-13.3 exists to make, and it was found by sabotage.**

    `remembered` was a parameter nothing filled, and removing
    `remembered=recall(...)` from this adapter changed no test outcome — the
    adapter tests covered translation and the repair tests supplied `remembered`
    by hand, so neither held both ends. This holds both ends.
    """
    store = FakeStore([a_repair_attempt("prefetch")])
    seen = wire_repair(monkeypatch, store)

    adapters.repair(repair_resources(store), chain_state)

    assert store.asked == ["shop.books.list"], "asked for this finding's memory"
    assert [item.patch.approach for item in seen["remembered"]] == ["prefetch"]


def test_the_repair_node_records_every_attempt_it_made(
    monkeypatch: pytest.MonkeyPatch, chain_state: CheckpointedState
) -> None:
    """**Including the one that worked.** S-11.7 can send it back after the
    Adversary breaks it, and an approach that passed its own test and failed the
    audit is exactly what the next attempt must not re-propose."""
    store = FakeStore()
    wire_repair(monkeypatch, store)

    adapters.repair(repair_resources(store), chain_state)

    assert [approach for _f, a in store.written for approach in [a.patch.approach]] == [
        "first",
        "second",
    ]
    assert {finding for finding, _a in store.written} == {"shop.books.list"}


# ==================================================== S-13.6 — §4 holds at any level


class LedgerStore:
    """A journal that records what `record_outcome` was told."""

    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str, str]] = []


def shipping_resources(store: LedgerStore) -> Resources:
    return Resources(
        workbench=cast(Any, FakeWorkbench()),
        sessions=lambda system: cast(Any, object()),
        client=unused(),
        budget=unused(),
        store=cast(Any, store),
        project="shop",
        trust_key="query-batching@django/postgres/1e2",
        revision="HEAD",
        root=Path(),
        python=["python"],
        ground=unused(),
        hands=unused(),
        bind=unused(),
        measure=unused(),
        instruments=unused(),
        executor=unused(),
        probe=unused(),
        source="shop/views.py",
        suite_command=["pytest"],
        metric="seconds",
        counters=[DB_QUERY],
        tokens=Tokens(prefix=8000, prompt=900),
    )


def parked_patch(*, slack_reducing: bool) -> CheckpointedState:
    outcome = Repaired(
        patch=a_patch(),
        classification=Classification(removals=()),
        attempts=(),
    )
    handover = dict(cast(dict[str, Any], _repaired(outcome, a_falsified())))
    handover["slack_reducing"] = slack_reducing
    return CheckpointedState(
        target="shop.books.list",
        repaired=cast(Any, handover),
        screening={"shop.books.list": {"flagged": True}},
    )


def test_a_slack_reducing_patch_is_withheld_even_when_the_gate_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**AC 2, and the reason it lives in the node.**

    S-13.6 lets a trusted project compile the ship gate away, and a compile-time
    decision cannot see a patch that does not exist yet. `00-BRIEF.md` §4 requires
    review for a slack-reducing patch *at any trust level*, so the refusal has to
    be here — after the patch exists.
    """
    store = LedgerStore()
    recorded: list[Any] = []
    monkeypatch.setattr(adapters, "record_outcome", lambda *a, **k: recorded.append(k))

    update = adapters.ship(shipping_resources(store), parked_patch(slack_reducing=True))

    assert "withheld" in str(update["flags"])
    assert "any trust level" in str(update["flags"])
    assert "screening" not in update, "nothing was invalidated, because nothing shipped"
    assert recorded == [], "and an outcome was not recorded for a patch that did not ship"


def test_a_patch_that_ships_records_a_clean_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """**AC 4.** S-13.4 built the levels and nothing moved them; without this the
    gate can only ever read `GATED` — a ledger that exists and is not written."""
    store = LedgerStore()
    recorded: list[Any] = []
    monkeypatch.setattr(adapters, "record_outcome", lambda *a, **k: recorded.append(k))

    update = adapters.ship(shipping_resources(store), parked_patch(slack_reducing=False))

    assert "shipped" in str(update["flags"])
    assert update["repaired"] is None
    assert [item["project"] for item in recorded] == ["shop"]
    assert [item["outcome"].name for item in recorded] == ["ACCEPTED"]


# ==================================================== S-7.14 — the ground node drives the loop


def grounding_resources() -> Resources:
    return Resources(
        workbench=cast(Any, FakeWorkbench()),
        sessions=lambda system: cast(Any, f"session for {system[:20]}"),
        client=cast(Any, "the client"),
        budget=unused(),
        store=unused(),
        project="shop",
        trust_key="query-batching@django/postgres/1e2",
        revision="HEAD",
        root=Path("/repos/shop"),
        python=["/venv/bin/python"],
        ground=cast(Any, lambda **_seams: FakeGrounded()),
        hands=cast(Any, "the hands"),
        bind=unused(),
        measure=unused(),
        instruments=unused(),
        executor=unused(),
        probe=unused(),
        source="shop/views.py",
        suite_command=["pytest"],
        metric="seconds",
        counters=[DB_QUERY],
        tokens=Tokens(prefix=8000, prompt=900),
    )


class FakeGrounded:
    """What the sequence produced, with only what the node reads off it."""

    def facts(self) -> dict[str, Any]:
        return {"root": "/repos/shop", "framework": "Django"}

    @property
    def workload(self) -> Workload:
        return HELPDESK_TICKETS


def wire_explore(monkeypatch: pytest.MonkeyPatch, exploration: Any) -> dict[str, Any]:
    """Replace the loop, leaving the node's own wiring. **The join, held at both ends.**

    Epic 7 has its own composition check for what `explore` does with these; what
    is checked here is that the node passes them at all. A story whose content is
    a join with no test of the join is how S-13.3's survived a sabotage, and this
    is the same shape one node along.
    """
    seen: dict[str, Any] = {}

    def fake_explore(*args: Any, **kwargs: Any) -> Any:
        seen["positional"] = args
        seen.update(kwargs)
        return exploration

    monkeypatch.setattr(adapters, "explore", fake_explore)
    return seen


class FakeExploration:
    def __init__(self, *, grounded: Any, steps: int) -> None:
        self.grounded = grounded
        self.steps = steps

    def report(self) -> str:
        return "Exploration: 4 step(s)\nGrounding failed: no database driver"


def test_the_ground_node_drives_the_explorer_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """**AC 1's production caller.** `ExperimentRef`, `gates_for` and the playbook
    read are all designed and unreachable; a fourth would be a pattern rather than
    an accident. The node that is named `ground` is what calls the loop, and it
    hands over the four things only a campaign knows: the checkout, its
    interpreter, the bound sequence and the hands that run a command."""
    seen = wire_explore(monkeypatch, FakeExploration(grounded=FakeGrounded(), steps=4))

    adapters.ground(grounding_resources(), CheckpointedState())

    assert seen["root"] == Path("/repos/shop")
    assert seen["python"] == ["/venv/bin/python"]
    assert callable(seen["ground"]), "the sequence, with the journal wired into it"
    assert seen["hands"] == "the hands"
    assert seen["measured_prefix_tokens"] == 8000
    assert seen["measured_prompt_tokens"] == 900


def test_the_explorers_session_is_keyed_on_the_prompt_it_owns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The session is the node's, not the loop's.** `Sessions` keys on the step's
    system prompt because that is what `refuse_shared_session` compares, so a loop
    that built its own would be the one agent whose cached prefix nobody checked."""
    seen = wire_explore(monkeypatch, FakeExploration(grounded=FakeGrounded(), steps=1))

    adapters.ground(grounding_resources(), CheckpointedState())

    session, client = seen["positional"]
    assert session == f"session for {proposal._SYSTEM[:20]}"
    assert client == "the client"
    assert owner_of(proposal._SYSTEM) is Agent.EXPLORER


def test_the_ground_node_writes_the_steps_the_learning_curve_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-13.5's first criterion had nothing to record: while grounding was nine
    mechanical stages run once each, *steps to ground* was the same number for
    every repository in the world."""
    wire_explore(monkeypatch, FakeExploration(grounded=FakeGrounded(), steps=7))

    update = adapters.ground(grounding_resources(), CheckpointedState())

    assert update["flags"] == [{"grounding_steps": 7}]
    assert update["project"] == {"root": "/repos/shop", "framework": "Django"}
    assert len(cast(list[Any], update["workloads"])) == 1


def test_a_repository_that_will_not_ground_is_a_null_result_and_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-7.11's acceptance: *reports failure rather than claiming success on empty
    data.* `00-BRIEF.md` §9 ships that as an answer, so the report reaches the
    channel a person reads rather than unwinding the graph."""
    wire_explore(monkeypatch, FakeExploration(grounded=None, steps=4))

    update = adapters.ground(grounding_resources(), CheckpointedState())

    assert "workloads" not in update, "nothing was ground, so nothing is claimed"
    assert "no database driver" in str(update["flags"])
    assert update["target"] is None


# ==================================================== S-13.7 — the journal reaches the sequence


def test_the_ground_node_wires_the_journal_into_the_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Four seams, and all four were unreachable before this.**

    `playbook_from_store` since S-13.1, `writer` since S-13.6, and
    `trusted_from_store` and `uses` from S-13.7. Each was written, tested and
    given no way to be filled, because the only object that could fill them held a
    repository and not a journal — and the key they file under is derived inside
    the sequence, so a caller could only bind one by fingerprinting the repository
    itself first.
    """
    seen: dict[str, Any] = {}

    def fake_ground(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return FakeGrounded()

    resources = grounding_resources()
    monkeypatch.setattr(
        adapters, "explore", lambda *a, **k: FakeExploration(grounded=k["ground"](), steps=1)
    )
    object.__setattr__(resources, "ground", fake_ground)

    adapters.ground(resources, CheckpointedState())

    assert set(seen) == {"playbook", "trusted_entries", "learn", "used"}
    assert all(callable(seam) for seam in seen.values())


def test_the_context_list_and_the_actionable_list_are_not_the_same_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The safety property, at the wiring.** `playbook` returns everything filed
    under the key including provisional entries and is what the Explorer is
    *shown*; `trusted_entries` returns only what three different projects agreed
    on. Wiring one lookup to both is the mistake this asserts against — it would
    hand `resolve_auth` a provisional entry to act on and nothing downstream could
    tell."""
    seen: dict[str, Any] = {}

    def fake_ground(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return FakeGrounded()

    resources = grounding_resources()
    monkeypatch.setattr(
        adapters, "explore", lambda *a, **k: FakeExploration(grounded=k["ground"](), steps=1)
    )
    object.__setattr__(resources, "ground", fake_ground)

    adapters.ground(resources, CheckpointedState())

    assert seen["playbook"] is not seen["trusted_entries"]


class JournalStub:
    """A journal holding rows in memory, with the two calls the seams make.

    Not a `PersistentStore`: what the wiring needs from one is `read` and
    `append`, and standing a Postgres container up to prove that a lookup reads
    the trusted list would be testing the database.
    """

    def __init__(self, rows: Sequence[Mapping[str, Any]] = ()) -> None:
        self.rows = list(rows)
        self.appended: list[tuple[str, Mapping[str, Any]]] = []

    def read(self, collection: Any, key: str | None = None) -> Sequence[Any]:
        del collection, key
        return [
            PersistentEntry(
                id=index,
                collection=Collection.PLAYBOOKS,
                key="Django/5",
                entry=cast(Any, row),
                written_at=datetime(2026, 8, 23, tzinfo=UTC),
            )
            for index, row in enumerate(self.rows)
        ]

    def append(self, collection: Any, key: str, entry: Mapping[str, Any]) -> Any:
        del collection
        self.appended.append((key, entry))
        return None


def earned() -> PlaybookEntry:
    return learned_from_auth(requirement="TOKEN", credential="TOKEN", resolved=True)


def unearned() -> PlaybookEntry:
    return learned_from_auth(requirement="SESSION", credential="SESSION", resolved=True)


def a_journal() -> JournalStub:
    """One entry three projects agreed on, and one nobody has used yet."""
    promoted, provisional = earned(), unearned()
    return JournalStub(
        [
            as_entry(promoted),
            as_entry(provisional),
            *(
                {"kind": "use", "of": promoted.digest(), "project": name, "worked": True}
                for name in ("shop", "blog", "billing")
            ),
        ]
    )


def wired(monkeypatch: pytest.MonkeyPatch, store: JournalStub) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_ground(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return FakeGrounded()

    resources = grounding_resources()
    object.__setattr__(resources, "store", store)
    object.__setattr__(resources, "ground", fake_ground)
    monkeypatch.setattr(
        adapters, "explore", lambda *a, **k: FakeExploration(grounded=k["ground"](), steps=1)
    )

    adapters.ground(resources, CheckpointedState())
    return seen


def test_the_actionable_lookup_the_node_wires_returns_only_promoted_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The join, held at both ends.** Wiring *a* callable is not wiring the
    trusted list: a lookup returning nothing satisfies every shape assertion and
    silently turns the memory off, and one returning everything hands
    `resolve_auth` a provisional entry to act on. Both are what this fails on."""
    seen = wired(monkeypatch, a_journal())

    assert list(seen["trusted_entries"]("Django/5")) == [earned()]


def test_the_context_lookup_the_node_wires_returns_the_provisional_ones_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same property. The Explorer is *shown* everything
    filed under the key; what it may act on is the shorter list."""
    seen = wired(monkeypatch, a_journal())

    shown = list(seen["playbook"]("Django/5"))

    assert len(shown) > 1, "provisional entries and uses are context"
    assert any(row.get("situation") == unearned().situation for row in shown)


def test_a_use_the_node_records_is_attributed_to_this_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`project` is what makes promotion mean *across different projects* rather
    than *often*, and `Resources.project` is the campaign's unit for exactly that
    reason — F15 reached from the ledger side, and this is the playbook side."""
    store = a_journal()
    seen = wired(monkeypatch, store)

    seen["used"]("Django/5", earned(), worked=False)

    key, row = store.appended[-1]
    assert key == "Django/5"
    assert row["project"] == "shop"
    assert row["worked"] is False
    assert row["of"] == earned().digest()
