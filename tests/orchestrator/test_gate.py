"""The human gate before a patch ships.

S-12.4. Three criteria, and they are three different kinds of claim: that the run
**stops** (a property of the compiled graph), that the person is shown **enough**
(a property of the state), and that the approval **survives an arbitrary delay**
(a property of the checkpoint).

The third is tested the only honest way — by throwing the process's objects away
between the stop and the resume. A test that kept the saver open would be
measuring whether an in-memory object still works, which it always does; what
S-12.4 promises is that a person can approve on Thursday, and Thursday is a
different process.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from coldfix.bench.stats import Growth
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.orchestrator.adapters import MissingInputError
from coldfix.orchestrator.checkpointing import for_development
from coldfix.orchestrator.gate import (
    Approval,
    GateError,
    NotAtTheGateError,
    pending,
    waiting_at,
)
from coldfix.orchestrator.graph import GraphError, Node, Wiring, assemble
from coldfix.orchestrator.resume import progress_of, resume, start
from coldfix.primitives.scaling import Distribution
from coldfix.repair.patch import Patch
from coldfix.state.checkpoint import CheckpointedState

DIFF = """\
--- a/shop/views.py
+++ b/shop/views.py
@@ -12,2 +12,2 @@
-    books = Book.objects.all()
+    books = Book.objects.select_related("author")
"""

UPDATES: Mapping[str, Mapping[str, object]] = {
    "ground": {"project": {"adapter": "django"}},
    "screen": {"screening": {"shop.books.list": {"flagged": True, "growth": {}}}},
    "investigate": {"target": "shop.books.list", "chain": None},
    "audit_finding": {"route": "REPAIR"},
    "repair": {"repaired": None},
    "audit_patch": {"route": "SHIP"},
    "ship": {"screening": {}, "route": None},
}


def build() -> Wiring:
    def make(name: str) -> Any:
        def step(state: CheckpointedState) -> Mapping[str, object]:
            return dict(UPDATES.get(name, {}))

        return step

    return Wiring(**{item.value: make(item.value) for item in Node})


# ============================================ AC 1 — the run stops before ship


def test_a_gated_run_parks_before_ship(tmp_path: Path) -> None:
    """**The criterion, performed.** `interrupt_before=["ship"]` means the run
    stops with `ship` as its next step rather than having taken it."""
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        graph = assemble(build(), saver)
        start(graph, "gated")

        assert progress_of(saver, "gated").started
        assert waiting_at(graph, "gated") == ("ship",)


def test_an_ungated_run_takes_the_step_the_gate_would_have_stopped(tmp_path: Path) -> None:
    """The control. Without the gate the same wiring runs `ship` and finishes, so
    the test above is measuring the gate rather than a wiring that never got
    there."""
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        graph = assemble(build(), saver, gated=False)
        start(graph, "open")

        assert waiting_at(graph, "open") == ()


def test_a_gated_graph_with_nowhere_to_park_is_refused() -> None:
    """`interrupt_before` parks the run *in the checkpoint*. With no checkpointer
    the run stops at `ship` and can never be resumed, so the approval a human
    gives has nothing to return to."""
    with pytest.raises(GraphError, match="gated graph needs a checkpointer"):
        assemble(build())


def test_there_is_no_trust_level_parameter() -> None:
    """**The absence is the enforcement**, which is `repair/slack.py`'s
    construction for the same reason.

    S-13.4's third criterion is that new projects start at level 0 regardless of
    cross-project history, so level 0 is the only value any project can be at
    until that ledger exists. A parameter here would have one reachable value —
    and the danger is not that nobody could flip it, but that somebody could,
    turning the gate off with no ledger to justify it.
    """
    parameters = set(inspect.signature(assemble).parameters)

    assert "trust" not in parameters
    assert "trust_level" not in parameters
    assert parameters == {"wiring", "checkpointer", "gated"}


# ============================================ AC 2 — what the human sees


def parked(**overrides: object) -> CheckpointedState:
    handover: dict[str, object] = {
        "patch": Patch(
            diff=DIFF, approach="select_related on the author", rationale="the sweep says so"
        ).model_dump(mode="json"),
        "slack_reducing": False,
    }
    fields: dict[str, Any] = {
        "target": "shop.books.list",
        "repaired": handover,
        "flags": [
            {
                "patch_audit": "SHIP — nothing survived the five attacks",
                "verdict": "clean: no attack found a way in",
                "before": {"seconds": 8.24, "queries": 1001.0},
                "after": {"seconds": 1.10, "queries": 2.0},
            }
        ],
    }
    fields.update(overrides)
    return CheckpointedState(**fields)


def test_the_approval_carries_all_four_things_the_criterion_names(chain: EvidenceChain) -> None:
    """AC 2 verbatim: the evidence chain, the patch, before/after measurements,
    and the Adversary verdict."""
    approval = pending(parked(chain=chain.model_dump(mode="json")))

    assert isinstance(approval, Approval)
    assert approval.chain.mechanism == chain.mechanism
    assert approval.patch.approach == "select_related on the author"
    assert approval.before["seconds"] == 8.24
    assert approval.after["seconds"] == 1.10
    assert "clean" in approval.verdict


def test_the_report_shows_the_change_in_a_form_a_reader_can_check(
    chain: EvidenceChain,
) -> None:
    rendered = pending(parked(chain=chain.model_dump(mode="json"))).render()

    assert "8.24 -> 1.1" in rendered
    assert "87% better" in rendered
    assert "1001 -> 2" in rendered
    assert DIFF.strip().splitlines()[-1] in rendered, "the diff itself, not a summary of it"


def test_a_metric_measured_on_one_side_only_is_not_rendered_as_an_improvement(
    chain: EvidenceChain,
) -> None:
    """**A metric that vanished is a measurement nobody took**, and showing it as
    a fall to zero would invent the most flattering number available."""
    state = parked(chain=chain.model_dump(mode="json"))
    audit = dict(state.flags[0])  # type: ignore[arg-type]
    audit["after"] = {"seconds": 1.10}

    rendered = pending(state.model_copy(update={"flags": [audit]})).render()

    assert "queries: measured on only one side" in rendered
    assert "100% better" not in rendered


def test_a_slack_reducing_patch_says_so_before_anything_else(chain: EvidenceChain) -> None:
    """`00-BRIEF.md` §4 requires the warning **prominently**, and a label under
    four screens of diff is not prominent. It is also not this gate's decision to
    overrule: no trust level clears it."""
    handover = dict(parked().repaired)  # type: ignore[arg-type]
    handover["slack_reducing"] = True
    approval = pending(parked(chain=chain.model_dump(mode="json"), repaired=handover))

    assert approval.blocked
    first = approval.render().splitlines()[0]
    assert "slack" in first.lower() or "SLACK" in first
    assert "no trust level clears this one" in approval.render()


# ============================================ AC 2 — and what stops a partial one


def test_a_run_that_never_reached_ship_is_not_an_approval() -> None:
    """*Not at the gate* and *at the gate with nothing to show* send a reader to
    two different places: one is a run still working, the other is a defect."""
    with pytest.raises(NotAtTheGateError, match="no patch is parked"):
        pending(CheckpointedState())


def test_a_parked_patch_with_no_evidence_is_refused_rather_than_rendered_blank(
    chain: EvidenceChain,
) -> None:
    """**Blanks are worse than an error here.** A person shown an approval with an
    empty evidence section reads it as *no evidence* rather than as *the report is
    broken* — and the first of those is a reason to reject a good patch."""
    del chain
    with pytest.raises(MissingInputError, match="no evidence to show"):
        pending(parked())


def test_a_parked_patch_the_adversary_never_saw_is_refused(chain: EvidenceChain) -> None:
    """Every route to `ship` runs through `audit_patch`, so this should be
    unreachable — and shipping on it would mean shipping a patch nothing
    attacked."""
    with pytest.raises(MissingInputError, match="no patch audit was recorded"):
        pending(parked(chain=chain.model_dump(mode="json"), flags=[]))


def test_the_verdict_shown_is_the_one_for_the_patch_that_is_parked(
    chain: EvidenceChain,
) -> None:
    """**The last audit, not the first.** S-11.7 sends a broken patch back and the
    second round appends another flag; showing the earliest would present a human
    with the verdict on a patch that was already replaced."""
    state = parked(chain=chain.model_dump(mode="json"))
    rounds = [
        {"patch_audit": "RETURN_TO_SURGEON", "verdict": "broken: the equivalence attack won"},
        *state.flags,
    ]

    assert "clean" in pending(state.model_copy(update={"flags": rounds})).verdict


# ============================================ AC 3 — Thursday is a different process


def test_an_approval_survives_the_process_that_produced_it(tmp_path: Path) -> None:
    """**AC 3, and the delay is not simulated — it is made irrelevant.**

    The run parks, every object it used is dropped, and a second `for_development`
    opens the same file from scratch. Nothing carries over but the checkpoint, so
    an hour and a fortnight are the same test.
    """
    store = tmp_path / "run.sqlite"

    with for_development(store) as saver:
        graph = assemble(build(), saver)
        start(graph, "thursday")
        assert waiting_at(graph, "thursday") == ("ship",)

    # Everything above is now closed. A new process, in every way that matters.
    with for_development(store) as reopened:
        reopened_graph = assemble(build(), reopened)
        assert waiting_at(reopened_graph, "thursday") == ("ship",), "still parked"

        final = resume(reopened_graph, reopened, "thursday")

        assert waiting_at(reopened_graph, "thursday") == (), "the gate let it through"
        assert final["screening"] == {}, "ship ran and cleared what it invalidated"


def test_resuming_a_parked_run_runs_ship_once(tmp_path: Path) -> None:
    """The gate is a pause, not a repeat. A resume that re-ran the whole graph
    would bill every phase again — S-12.3's `invoke(None, ...)` finding, reached
    through the interrupt rather than through a crash."""
    store = tmp_path / "run.sqlite"
    visits: list[str] = []

    def counting() -> Wiring:
        def make(name: str) -> Any:
            def step(state: CheckpointedState) -> Mapping[str, object]:
                visits.append(name)
                return dict(UPDATES.get(name, {}))

            return step

        return Wiring(**{item.value: make(item.value) for item in Node})

    with for_development(store) as saver:
        start(assemble(counting(), saver), "once")
        before = list(visits)

        resume(assemble(counting(), saver), saver, "once")

    assert "ship" not in before, "the gate stopped it"
    assert visits[len(before) :] == ["ship"], "the resume took exactly the parked step"


@pytest.fixture
def chain() -> EvidenceChain:
    """A real chain, because `pending` validates one and a stub would only prove
    that the stub validates.

    Built here rather than shared: every test file in this project that needs one
    builds its own, and hoisting them into a fixture module is a change to test
    infrastructure that S-12.4 has no reason to make.
    """
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 2.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 2 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24, "seconds.share_removed": 0.89, "rows": 1000.0},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the serializer removed 89% of wall time",
    )
    conditions = Conditions.of(
        fixture_shape=Distribution.UNIFORM.value,
        platform="x86_64-linux",
        concurrency=1,
        scales=[10, 100, 1000],
    )
    return EvidenceChain.assemble(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        exclusions=[Exclusion(experiment=excluded, conditions=conditions)],
        localization=[
            LocalizationLink(
                scope="BookSerializer.to_representation",
                experiment=confirmed,
                share_of_cost=0.89,
                basis="8.24s baseline against 0.90s ablated",
            )
        ],
        mechanism="the serializer re-renders the author for every book",
        complexity={"rows": Growth.LINEAR},
        site=Site(path="shop/serializers.py", first_line=41, last_line=52),
        context=[Implicated(path="shop/models.py", reason="declares the Author relation")],
    )


def test_the_gate_error_hierarchy_lets_a_caller_tell_the_two_apart() -> None:
    """A caller polling for approvals catches one; a caller reporting a defect
    catches the other. Collapsing them would make *still running* and *broken*
    the same exception."""
    assert issubclass(NotAtTheGateError, GateError)
    assert not issubclass(MissingInputError, GateError)
    assert re.search(r"\w", NotAtTheGateError.__doc__ or "")
