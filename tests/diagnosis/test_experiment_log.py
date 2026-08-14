"""S-8.4 — the experiment log, and the three things it has to guarantee.

Append-only is the non-negotiable with a number attached — `CLAUDE.md` says
re-summarizing mid-investigation multiplies cost by about twenty — so the tests
that matter here are the ones that attempt the violation: reorder it, retract an
entry, rebuild it a different way and see whether the bytes move.

"Cache-friendly" is checked as the property S-5.7's prefix cache actually needs:
**the rendered log at N entries is a byte prefix of the rendered log at N+1.**
Asserting anything weaker would be asserting that it looks tidy.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from coldfix.cost.pruning import PrunedLog
from coldfix.diagnosis.log import (
    Experiment,
    ExperimentLog,
    ExperimentLogError,
    Verdict,
)


def logged(log: ExperimentLog, **overrides: object) -> Experiment:
    """Append one experiment, varying only what a test is about."""
    fields: dict[str, object] = {
        "hypothesis": "the author lookup is an N+1",
        "primitive": "scale_volume",
        "target": "shop.books.list",
        "design": "sweep n in (10, 100), count db.query",
        "measurement": {"db.query": 101.0, "seconds": 0.08},
        "verdict": Verdict.CONFIRMED,
        "outcome": "queries rose 11 to 101 across 10x rows",
    }
    fields.update(overrides)
    return log.append(**fields)  # type: ignore[arg-type]


# ================================== AC 1: every experiment carries all five parts


def test_an_experiment_records_the_five_things_a_finding_needs() -> None:
    log = ExperimentLog()

    experiment = logged(log)

    assert experiment.hypothesis
    assert experiment.primitive == "scale_volume"
    assert experiment.design
    assert experiment.measurement == {"db.query": 101.0, "seconds": 0.08}
    assert experiment.verdict is Verdict.CONFIRMED


def test_an_experiment_without_a_measurement_is_refused() -> None:
    """The first non-negotiable, enforced where the record enters rather than
    where an evidence chain is assembled three stories later."""
    with pytest.raises(ExperimentLogError, match="reading code"):
        logged(ExperimentLog(), measurement={})


def test_an_experiment_without_a_hypothesis_is_refused() -> None:
    with pytest.raises(ExperimentLogError):
        logged(ExperimentLog(), hypothesis="")


def test_an_experiment_without_a_design_is_refused() -> None:
    with pytest.raises(ExperimentLogError):
        logged(ExperimentLog(), design="")


def test_indexes_are_assigned_in_order_and_are_one_based() -> None:
    """`read_experiment(1)` has to mean the first experiment. A caller-supplied
    index can collide, skip or restart, and each makes a retrieval return
    somebody else's measurement with no error."""
    log = ExperimentLog()

    first = logged(log)
    second = logged(log, hypothesis="something else")

    assert (first.index, second.index) == (1, 2)


def test_an_experiment_can_be_read_back_by_index() -> None:
    log = ExperimentLog()
    logged(log)
    logged(log, hypothesis="the serializer is the cost")

    assert log.experiment(2).hypothesis == "the serializer is the cost"


def test_reading_an_index_that_is_not_there_is_an_error_not_a_none() -> None:
    """Every caller of this is about to read a measurement out of it, and a
    `None` would reach the arithmetic."""
    with pytest.raises(ExperimentLogError, match="no experiment 4"):
        ExperimentLog().experiment(4)


# ============================== AC 2: never reordered and never re-summarized


def test_the_log_has_no_way_to_reorder_summarize_or_forget() -> None:
    """The non-negotiable expressed as an absence. A method that cannot be called
    is a guarantee; a comment asking callers not to call one is a request.

    This fails the moment somebody adds one for a demo, which is why it is
    asserted by inspection rather than described in a docstring.
    """
    surface = {name for name in dir(ExperimentLog) if not name.startswith("_")}

    assert not surface & {"reorder", "summarize", "replace", "forget", "evict", "truncate"}


def test_the_experiments_view_cannot_be_reordered_through() -> None:
    """It hands back a copy. A caller sorting the sequence it was given must not
    be sorting the log."""
    log = ExperimentLog()
    logged(log)
    logged(log, hypothesis="second")

    view = log.experiments
    assert isinstance(view, tuple)
    assert [entry.index for entry in log.experiments] == [1, 2]


def test_an_experiment_is_frozen_once_recorded() -> None:
    log = ExperimentLog()
    experiment = logged(log)

    # No `type: ignore`, and it is the same finding ADR 077 recorded: pydantic's
    # plugin does not model `frozen=True` as a signature, so this assignment
    # type-checks and fails only at runtime. The guarantee is the schema.
    with pytest.raises(ValidationError):
        experiment.outcome = "something more flattering"


def test_appending_never_changes_an_earlier_record() -> None:
    log = ExperimentLog()
    first = logged(log)
    before = first.digest()

    for index in range(5):
        logged(log, hypothesis=f"hypothesis {index}")

    assert log.experiment(1).digest() == before


# ======================== AC 3: serialization is stable and cache-friendly


def test_the_rendered_log_at_n_is_a_byte_prefix_of_the_log_at_n_plus_one() -> None:
    """The only property S-5.7's prefix cache actually needs, and the reason the
    non-negotiable has a cost attached: a log that rewrites its own earlier bytes
    invalidates the cached prefix and multiplies the bill.
    """
    log = ExperimentLog()
    renders = []

    for index in range(6):
        logged(log, hypothesis=f"hypothesis {index}", outcome=f"outcome {index}")
        renders.append(log.render())

    for earlier, later in itertools.pairwise(renders):
        assert later.startswith(earlier), "appending rewrote the log's earlier bytes"


def test_the_detail_never_reaches_the_rendered_log() -> None:
    """S-5.8's whole argument: the full output is held always and rendered never,
    because writing it into the log would invalidate the cached prefix."""
    log = ExperimentLog()
    logged(log, detail="a hundred lines of stack" * 100)

    assert "a hundred lines of stack" not in log.render()
    assert "a hundred lines of stack" in log.read_experiment(1)


def test_retrieving_detail_changes_nothing_about_the_log() -> None:
    log = ExperimentLog()
    logged(log, detail="raw counters")
    before = log.render()

    log.read_experiment(1)

    assert log.render() == before


def test_a_digest_is_a_property_of_the_record_not_of_how_it_was_built() -> None:
    """Two processes that recorded the same experiment must agree on it."""
    one = ExperimentLog()
    other = ExperimentLog()

    assert logged(one).digest() == logged(other).digest()


def test_two_different_experiments_do_not_share_a_digest() -> None:
    """The control. A digest that ignored its input would pass the test above."""
    log = ExperimentLog()

    assert logged(log).digest() != logged(log, outcome="queries flat").digest()


def test_the_logs_digest_changes_when_and_only_when_it_grows() -> None:
    log = ExperimentLog()
    logged(log)
    before = log.digest()

    assert log.digest() == before
    logged(log, hypothesis="another")
    assert log.digest() != before


def test_the_digest_is_stable_across_interpreters() -> None:
    """The guarantee a digest actually has is that a fresh process computes the
    same one — asserting it twice in this process proves only that equal inputs
    hash equally, which S-4.1 recorded after a sabotage walked through exactly
    that test."""
    program = (
        "from coldfix.diagnosis.log import ExperimentLog, Verdict;"
        "log=ExperimentLog();"
        "log.append(hypothesis='h', primitive='p', target='t', design='d',"
        " measurement={'db.query': 2.0}, verdict=Verdict.NARROWED, outcome='o');"
        "print(log.digest())"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=300, check=True
    )

    here = ExperimentLog()
    here.append(
        hypothesis="h",
        primitive="p",
        target="t",
        design="d",
        measurement={"db.query": 2.0},
        verdict=Verdict.NARROWED,
        outcome="o",
    )

    assert result.stdout.strip() == here.digest()


def test_an_experiment_round_trips_through_json() -> None:
    """It has to: S-6.3 stores a reference and the measurement lives here, so a
    record that did not survive serialization would take the evidence with it."""
    log = ExperimentLog()
    experiment = logged(log)

    reloaded = Experiment.model_validate(json.loads(experiment.model_dump_json()))

    assert reloaded == experiment


# ============================ there is one log, not two (Epic 5's own defect)


def test_the_log_wraps_the_pruned_log_rather_than_shadowing_it() -> None:
    """Epic 5's composition found two append-only logs, and the failure was
    silent: caching is a prefix match, so a log wrong in *content* but still
    append-only reports full cache hits and a rising bill with nothing failing.
    """
    log = ExperimentLog()
    logged(log)
    logged(log, hypothesis="second")

    assert isinstance(log.pruned, PrunedLog)
    assert [record.index for record in log.pruned.records] == [1, 2]
    assert log.render() == log.pruned.render()


def test_the_artifact_and_the_summary_are_filed_under_the_same_index() -> None:
    """One entry point for both, so they cannot drift. Two collections that must
    stay in step and can be written separately is the shape of the defect this
    module exists to avoid."""
    log = ExperimentLog()
    logged(log, target="a")
    logged(log, target="b")

    for record, experiment in zip(log.records, log.experiments, strict=True):
        assert record.index == experiment.index
        assert record.target == experiment.target


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hypothesis", ""),
        ("design", ""),
        ("measurement", {}),
        ("outcome", "x" * 5000),
        ("target", ""),
    ],
)
def test_a_refused_append_leaves_nothing_behind(field: str, value: object) -> None:
    """The defect this ordering exists to prevent, found by running it.

    An append-only log cannot retract an entry — that is what append-only means —
    so if the summary is taken before the artifact is validated, a record with an
    empty hypothesis leaves a summary in the **rendered prompt** with no
    experiment behind it. Nothing raises at the point where the two stop
    agreeing; the prompt simply shows an experiment this log cannot produce a
    measurement for.

    Every way of being refused is checked, because the two collections are
    validated by different rules and only some of them fired first.
    """
    log = ExperimentLog()
    logged(log)
    before = log.render()

    with pytest.raises(ExperimentLogError):
        logged(log, **{field: value})

    assert len(log.experiments) == 1
    assert len(log.records) == 1
    assert log.render() == before


def test_the_two_collections_never_disagree_on_length() -> None:
    """The invariant behind the ordering, asserted directly: a summary with no
    artifact is a prompt entry nobody can retrieve a measurement for."""
    log = ExperimentLog()

    for index in range(4):
        logged(log, hypothesis=f"h{index}")
        with pytest.raises(ExperimentLogError):
            logged(log, measurement={})

    assert len(log.experiments) == len(log.records) == 4


def test_the_verdict_vocabulary_is_the_three_the_backlog_names() -> None:
    assert [verdict.value for verdict in Verdict] == ["confirmed", "narrowed", "rejected"]


def test_narrowed_is_the_verdict_that_does_not_close_a_hypothesis() -> None:
    """Collapsing it into `rejected` would throw away the half of the search
    space the experiment bought."""
    assert Verdict.CONFIRMED.settled
    assert Verdict.REJECTED.settled
    assert not Verdict.NARROWED.settled


def test_the_report_reads_in_the_order_things_happened() -> None:
    log = ExperimentLog()
    logged(log, primitive="scale_volume", verdict=Verdict.REJECTED, outcome="queries flat")
    logged(log, primitive="ablation", verdict=Verdict.CONFIRMED, outcome="serializer is 80%")

    described = log.describe()

    assert described.index("scale_volume") < described.index("ablation")
    assert "2 experiment(s)" in described
