"""EPIC 16 COMPOSITION CHECK — what a run concluded becomes what a person reads.

Three stories render three things: the evidence chain (S-16.1), the pull request
(S-16.2), the null result (S-16.3). Each is tested against artifacts built by
hand. **Nothing had ever walked from a screen to a document**, and the walk is
where this epic's sentence actually lives.

Doing it found the screening node deciding *what is worth investigating* for
itself, with a rule that clears the defect this project was built around. The
first test below is that defect, measured on the planted fixture rather than
argued: `flag()` returns `db.query=GROWTH` and the node used to return nothing.

**The node had never been driven.** `test_graph.py` tests the router that reads
`screening`, and `test_adapters.py` tested the helper that wrote it — against a
stand-in metric object, asserting the wrong answer with a plausible reason
attached. Between the two there was no test that ran the node and read what it
produced, which is the shape every composition check in this project has found.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.orchestrator.adapters import Resources, Tokens, screen
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.assess import conclude
from coldfix.screening.flagging import flag
from coldfix.screening.growth import screen as screen_workloads
from coldfix.screening.null import NullResult
from coldfix.screening.workload import BoundWorkload, FixtureRecipe, Workload
from coldfix.state.checkpoint import CheckpointedState
from fixtures.planted.queries import (
    list_books_batched,
    list_books_n_plus_one,
    summarize_with_fixed_floor,
)
from fixtures.planted.store import Store
from fixtures.planted.subject import StoreReset, Subject


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(DB_QUERY, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(DB_QUERY)


def _bound(name: str, call: Any) -> BoundWorkload:
    """One planted workload, bound the way an adapter would bind it."""
    subject = Subject(call)
    descriptor = Workload(
        id=name,
        description=f"the planted {name} workload",
        entry_point=f"fixtures.planted.queries.{call.__name__}",
        fixture=FixtureRecipe(
            entity="author",
            per_parent=2,
            distribution=Distribution.UNIFORM,
            source="fixtures.planted.store.build_store",
            seed=0,
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    )
    return BoundWorkload(
        descriptor,
        invoke=subject.invoke,
        scale=subject.scale,
        reset=VerifiedReset(
            mechanism=StoreReset(subject),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        process_identity=subject.process_identity,
        extra_counters=subject.payload,
    )


def _screened(bindings: Sequence[BoundWorkload]) -> Any:
    """Screen the way the node does, counters and all.

    Written as a helper after the first draft of these tests called
    `screen_workloads(bindings)` and reproduced the very blindness they were
    written to catch: with no counters attached there is no `db.query` to fit,
    so `flag` returned nothing and the comparison passed against a node that
    was also returning nothing.
    """
    return screen_workloads(bindings, counters=[DB_QUERY])


def _unused() -> Any:
    """A resource this node never touches. Named so a surprise is loud."""

    def refuse(*_args: object, **_kwargs: object) -> object:
        message = "the screen node reached a resource it should not need"
        raise AssertionError(message)

    return refuse


def _resources(bindings: Sequence[BoundWorkload]) -> Resources:
    """Everything `screen` needs and nothing it does not. **No model client.**

    Screening makes no model calls, which is Epic 4's own goal statement — *find
    what is worth investigating using zero model calls* — so every session and
    client here refuses. A node that started needing one would fail loudly rather
    than quietly costing money.
    """
    return Resources(
        workbench=cast(Any, _unused()),
        sessions=cast(Any, _unused()),
        client=cast(Any, _unused()),
        budget=cast(Any, _unused()),
        store=cast(Any, _unused()),
        project="shop",
        trust_key="query-batching@planted/1e2",
        revision="HEAD",
        root=Path(),
        python=["python"],
        ground=cast(Any, _unused()),
        hands=cast(Any, _unused()),
        bind=lambda _workloads: bindings,
        measure=cast(Any, _unused()),
        instruments=cast(Any, _unused()),
        executor=cast(Any, _unused()),
        probe=cast(Any, _unused()),
        source="fixtures/planted/queries.py",
        suite_command=["pytest"],
        metric="seconds",
        counters=[DB_QUERY],
        tokens=Tokens(prefix=8000, prompt=900),
    )


def _state(*bindings: BoundWorkload) -> CheckpointedState:
    return CheckpointedState(
        workloads=[binding.descriptor.model_dump(mode="json") for binding in bindings]
    )


# ================================= the defect: the node decided this for itself


def test_the_pipeline_flags_the_planted_n_plus_one(query_counter: None) -> None:
    """**The defect this composition check found, driven through the node.**

    An N+1 grows *linearly* in query count — it is one query per author — so a
    rule that flags superlinear growth clears it. The node held such a rule, in a
    local `_SUSPICIOUS` set, and `screening/flagging.py` exists because that
    reading is wrong: the verdict is the fit against each metric's own
    *expectation*, and a round-trip count is expected to stay constant.

    Measured before the fix: `flag()` returned `db.query=GROWTH` and the node
    returned `flagged: False` for the same workload.
    """
    binding = _bound("n.plus.one", list_books_n_plus_one)

    written = screen(_resources([binding]), _state(binding))

    screening = cast(Mapping[str, Any], written["screening"])
    assert screening["n.plus.one"]["flagged"] is True
    assert screening["n.plus.one"]["growth"]["db.query"] == "LINEAR"

    # **Which metric flagged, not merely that one did.** The first version of
    # this test asserted the boolean, and a sabotage restoring the old
    # superlinear-only rule *passed* it: this workload's `seconds` sometimes
    # fits superlinear on timing noise, so the wrong rule reached the right
    # answer by accident. The query count is the finding.
    assert screening["n.plus.one"]["flagged_metrics"] == [DB_QUERY]


def test_the_node_and_the_flagger_agree_on_every_planted_workload(
    query_counter: None,
) -> None:
    """The general form, so the fix is not one special case.

    Whatever `flag` says about a workload is what the node must record. Any
    disagreement is the pipeline holding a second opinion about the question
    Epic 4 answers, which is how the first one went unnoticed.
    """
    bindings = [
        _bound("n.plus.one", list_books_n_plus_one),
        _bound("batched", list_books_batched),
        _bound("fixed.floor", summarize_with_fixed_floor),
    ]

    written = screen(_resources(bindings), _state(*bindings))
    screening = cast(Mapping[str, Any], written["screening"])

    for result in _screened(bindings):
        expected = bool(flag(result))
        assert screening[result.workload.id]["flagged"] is expected, result.workload.id


def test_the_flat_cost_decoy_is_not_flagged_through_the_node(query_counter: None) -> None:
    """The metastability trap, guarded where a run would meet it.

    `summarize_with_fixed_floor` issues 37 queries at any volume, modelled on the
    ~35-query floor S-0.3 measured on a real mature system, and the fixture
    README is explicit that flagging it is the trap `00-BRIEF.md` §4 is about.
    The flat-cost threshold is 120, so it stays quiet — and the node has to agree
    with `flag` about that, not merely about the positive case.

    **This test was written the other way round first**, asserting the decoy
    *was* flagged because it is the flat-cost fixture. It is the flat-cost
    fixture and it sits below the threshold on purpose; the assertion was about
    what the author expected rather than what the fixture measures.
    """
    binding = _bound("fixed.floor", summarize_with_fixed_floor)
    assert not flag(_screened([binding])[0]), "37 queries is under the 120 threshold"

    written = screen(_resources([binding]), _state(binding))

    assert "screening" not in written, "nothing flagged is a null result, not an empty plan"
    reported = cast(Sequence[Mapping[str, str]], written["flags"])[0]["null_result"]
    assert "db.query constant at 37" in reported


# ============================================ the order the ranking decided


def test_the_node_records_the_position_the_plan_gave_each_workload(
    query_counter: None,
) -> None:
    """`order` is `Plan.investigate`'s index, not the alphabet's.

    `rank` puts growth flags ahead of flat-cost ones as a class and orders by
    magnitude within each, because the two are measured in different units and
    there is no honest exchange rate between them. The node used to write no
    order at all and `_first_flagged` read the set back by name, discarding it.

    The ordering itself is exercised against synthetic state in
    `test_adapters.py`, where two flagged workloads can be arranged to disagree
    with alphabetical order; what this holds is the other end — that the number
    the node writes is the one the plan decided.
    """
    bindings = [
        _bound("z.n.plus.one", list_books_n_plus_one),
        _bound("a.batched", list_books_batched),
    ]

    written = screen(_resources(bindings), _state(*bindings))
    screening = cast(Mapping[str, Any], written["screening"])

    planned = conclude(_screened(bindings))
    assert not isinstance(planned, NullResult)
    for position, name in enumerate(planned.investigate):
        assert screening[name]["order"] == position
    assert screening["a.batched"]["order"] is None, "an unflagged workload has no position"


# ================================================== the null branch, end to end


def test_a_clean_screen_reports_the_null_result_artifact(query_counter: None) -> None:
    """The document, not a sentence somebody wrote at the node.

    `screen` used to end a clean run with a hand-written string. Everything S-4.5
    and S-16.3 put on the artifact — the thresholds, the per-workload conditions,
    the measured basis for each covered metric — stopped at the node that
    produced it.
    """
    binding = _bound("batched", list_books_batched)

    written = screen(_resources([binding]), _state(binding))

    assert written["target"] is None
    reported = cast(Sequence[Mapping[str, str]], written["flags"])[0]["null_result"]
    assert "Screened 1 workloads and flagged none" in reported
    assert "Thresholds applied:" in reported
    assert "Nothing was flagged, and this is what was measured:" in reported
    assert "db.query constant" in reported


def test_the_two_branches_are_exclusive_through_the_node(query_counter: None) -> None:
    """A run either has something to investigate or a null result. Never both.

    `conclude` makes that exclusive by construction; this asserts the node did
    not reintroduce a third state by writing a screening channel *and* a null
    result, which is what a hand-rolled branch does when the two halves are
    written at different times.
    """
    flagged = _bound("n.plus.one", list_books_n_plus_one)
    clean = _bound("batched", list_books_batched)

    with_finding = screen(_resources([flagged]), _state(flagged))
    without = screen(_resources([clean]), _state(clean))

    assert "screening" in with_finding
    assert "null_result" not in str(with_finding.get("flags", ""))
    assert "screening" not in without
    assert isinstance(conclude(_screened([clean])), NullResult)
