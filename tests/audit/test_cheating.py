"""Epic 11, S-11.3 — cheat detection.

*Checks for cached state across runs, deferred work, over-fetching, stubbed
responses, shape-specific special-casing. Verifies the improvement survives a
fresh process.*

The measurements are supplied, because `CLAUDE.md` puts measuring in the harness
and this module is an auditor. What is under test is the arithmetic and — much
more importantly — **what it refuses to conclude**: four of the five classes need
a metric only some adapters produce, and an audit that skipped those and reported
the rest as passing would read as five checks when it was one.
"""

from __future__ import annotations

import pytest

from coldfix.audit import cheating
from coldfix.audit.cheating import (
    RESIDUE,
    CheatAudit,
    CheatError,
    Check,
    Finding,
    Measure,
    Metrics,
    Reading,
    Revision,
    detect,
)
from coldfix.audit.equivalence import (
    AdversarialInput,
    Equivalence,
    Observed,
    Outcome,
    Probed,
    ReproducingInput,
    Shape,
    compare_outputs,
)
from coldfix.primitives.measurement import MetricKind
from coldfix.primitives.scaling import Distribution
from coldfix.repair import falsification
from coldfix.repair.falsification import Cheat

SECONDS = "seconds"
QUERIES = "db.query"
ROWS = "rows"
TOTAL = "process.seconds"
SIZE = "response.bytes"

KINDS = {
    SECONDS: MetricKind.DURATION,
    QUERIES: MetricKind.COUNT,
    ROWS: MetricKind.COUNT,
    TOTAL: MetricKind.DURATION,
    SIZE: MetricKind.COUNT,
}

COST_ONLY = Metrics(cost=SECONDS, kinds=KINDS)
EVERYTHING = Metrics(
    cost=SECONDS,
    kinds=KINDS,
    calls=QUERIES,
    work=ROWS,
    whole_process=TOTAL,
    response_size=SIZE,
)


def reading(
    revision: Revision,
    *,
    shape: Distribution = Distribution.UNIFORM,
    first: dict[str, float] | None = None,
    repeated: int = 0,
    warm: dict[str, float] | None = None,
) -> Reading:
    """One fresh process. `warm` is what each pass after the first measured."""
    cold = first if first is not None else {SECONDS: 10.0}
    later = warm if warm is not None else cold
    return Reading(
        revision=revision,
        shape=shape,
        first=cold,
        repeated=tuple(dict(later) for _ in range(repeated)),
    )


def measuring(readings: dict[tuple[Revision, Distribution], Reading]) -> Measure:
    """A harness that hands back what the test decided each condition measured."""

    def measure(revision: Revision, shape: Distribution) -> Reading:
        return readings[(revision, shape)]

    return measure


def pair(
    before: Reading, after: Reading, *, shape: Distribution = Distribution.UNIFORM
) -> dict[tuple[Revision, Distribution], Reading]:
    return {(Revision.ORIGINAL, shape): before, (Revision.PATCHED, shape): after}


def found(audit: CheatAudit, cheat: Cheat) -> Check:
    (check,) = [item for item in audit.checks if item.cheat is cheat]
    return check


def an_equivalence(*, broken: bool) -> Equivalence:
    """S-11.2's artifact, either surviving or carrying an objection."""
    payload = AdversarialInput(shape=Shape.EMPTY, label="an empty list", payload=[])
    if not broken:
        return Equivalence(
            workload="w",
            probed=(Probed(payload, Outcome.MATCHED, "identical"),),
            reproducing=(),
            runs=2,
        )
    divergence = compare_outputs([{"id": 1, "name": "a"}], [{"id": 1}])
    assert divergence is not None
    reproducing = ReproducingInput(
        input=payload,
        before=[{"id": 1, "name": "a"}],
        after=Observed(payload=[{"id": 1}], wall_seconds=0.1),
        divergence=divergence,
        program="print()",
    )
    return Equivalence(
        workload="w",
        probed=(Probed(payload, Outcome.DIFFERED, "the name is gone"),),
        reproducing=(reproducing,),
        runs=4,
    )


# ============ the five classes are one vocabulary, not two


def test_the_classes_are_s_10_1s_enum_and_not_a_second_spelling() -> None:
    """S-10.1 built `Cheat` as an enum for this story in as many words. A private copy
    here would be two vocabularies at the one join that has to agree — a test saying
    what it catches, and an audit saying what it found.

    **Asserted on the object, not on the source.** `"class Cheat" not in source` was
    the first attempt and it matched `class CheatError`, which is the
    substring-over-source trap this project has now walked into four times. Identity
    cannot be fooled by a name that starts the same way.
    """
    bound = vars(cheating)["Cheat"]
    assert bound is falsification.Cheat
    assert [item.name for item in bound] == [item.name for item in Cheat]


def test_every_class_appears_in_every_audit() -> None:
    audit = detect(
        measuring(pair(reading(Revision.ORIGINAL), reading(Revision.PATCHED))),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )
    assert [check.cheat for check in audit.checks] == list(Cheat)
    assert len(Cheat) == 5


def test_an_audit_missing_a_class_cannot_be_built() -> None:
    """A shorter list reading as a complete one. This is the artifact a verdict reads,
    so the constructor is where it has to be caught."""
    with pytest.raises(CheatError, match="shorter list reading as a complete one"):
        CheatAudit(
            metrics=COST_ONLY,
            checks=(Check(Cheat.CACHED_STATE, Finding.NOT_DETECTED, "no"),),
            original=reading(Revision.ORIGINAL),
            patched=reading(Revision.PATCHED),
            relative_noise=0.12,
        )


# ============ a question that could not be asked is not one that came back clean


def test_the_four_classes_that_need_a_metric_are_untested_without_it() -> None:
    """**The failure an obvious implementation commits.** Skip what you cannot measure
    and report the rest as passing, and *five checks, nothing found* means *one
    check, nothing found*."""
    audit = detect(
        measuring(
            pair(reading(Revision.ORIGINAL, repeated=2), reading(Revision.PATCHED, repeated=2))
        ),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )

    assert {check.cheat for check in audit.untested} == {
        Cheat.DEFERRED_WORK,
        Cheat.OVER_FETCH,
        Cheat.STUBBED_RESPONSE,
        Cheat.SHAPE_SPECIFIC,
    }
    assert not audit.detected
    assert not audit.complete
    assert not audit.clean, "nothing was found because almost nothing was looked for"
    assert "never checked" in audit.describe()


def test_a_full_sweep_that_finds_nothing_is_a_null_result() -> None:
    """The other side of the same property: when every question *was* asked and none
    answered yes, that ships."""
    honest = {SECONDS: 4.0, QUERIES: 3.0, ROWS: 100.0, TOTAL: 6.0, SIZE: 2048.0}
    original = {SECONDS: 10.0, QUERIES: 101.0, ROWS: 100.0, TOTAL: 12.0, SIZE: 2048.0}
    readings = pair(
        reading(Revision.ORIGINAL, first=original, repeated=2),
        reading(Revision.PATCHED, first=honest, repeated=2),
    )
    readings[(Revision.ORIGINAL, Distribution.LONG_TAIL)] = reading(
        Revision.ORIGINAL, shape=Distribution.LONG_TAIL, first=original
    )
    readings[(Revision.PATCHED, Distribution.LONG_TAIL)] = reading(
        Revision.PATCHED, shape=Distribution.LONG_TAIL, first=honest
    )

    audit = detect(
        measuring(readings),
        metrics=EVERYTHING,
        shape=Distribution.UNIFORM,
        alternatives=[Distribution.LONG_TAIL],
    )
    assert audit.complete
    assert not audit.detected
    assert audit.clean
    assert audit.survives_a_fresh_process
    assert "null result" in audit.describe()


def test_the_report_states_what_no_counter_here_can_see() -> None:
    audit = detect(
        measuring(pair(reading(Revision.ORIGINAL), reading(Revision.PATCHED))),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )
    assert RESIDUE in audit.describe()


# ============ AC 2 — the improvement survives a fresh process


def test_a_gain_that_exists_warm_and_not_cold_does_not_survive() -> None:
    """**AC 2.** The patch makes the second run through a process cheap and leaves the
    first alone. Measured warm it is a 5x win; measured where a real user arrives it
    is nothing."""
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0}, repeated=3, warm={SECONDS: 10.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 10.0}, repeated=3, warm={SECONDS: 2.0})

    audit = detect(
        measuring(pair(original, patched)), metrics=COST_ONLY, shape=Distribution.UNIFORM
    )
    assert audit.survives_a_fresh_process is False
    assert not audit.clean
    assert "does not survive a fresh process" in audit.describe()


def test_a_gain_present_on_the_cold_pass_survives() -> None:
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0}, repeated=3, warm={SECONDS: 9.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 3.0}, repeated=3, warm={SECONDS: 2.5})

    audit = detect(
        measuring(pair(original, patched)), metrics=COST_ONLY, shape=Distribution.UNIFORM
    )
    assert audit.survives_a_fresh_process is True


def test_a_workload_driven_once_leaves_the_question_unasked(tmp_path: object) -> None:
    """`None`, not `False`. Reporting an unaskable question as a failure would block
    every patch on a harness that ran the workload once."""
    audit = detect(
        measuring(
            pair(
                reading(Revision.ORIGINAL, first={SECONDS: 10.0}),
                reading(Revision.PATCHED, first={SECONDS: 3.0}),
            )
        ),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )
    assert audit.survives_a_fresh_process is None
    assert "nothing to compare a cold pass against" in audit.describe()
    # And the cached-state class with it: there is no second run in the process to
    # be cheaper than the first, so a warm-up of zero would be a fact about the
    # harness rather than about the patch.
    assert found(audit, Cheat.CACHED_STATE).finding is Finding.UNTESTED
    assert (
        Reading(
            revision=Revision.PATCHED, shape=Distribution.UNIFORM, first={SECONDS: 10.0}
        ).warm_up(SECONDS)
        is None
    )


def test_a_patch_with_no_warm_gain_at_all_is_not_accused_of_not_surviving() -> None:
    """There was no gain to survive. `False` here would report every patch that did not
    speed the workload up as a caching cheat."""
    flat = {SECONDS: 10.0}
    audit = detect(
        measuring(
            pair(
                reading(Revision.ORIGINAL, first=flat, repeated=2),
                reading(Revision.PATCHED, first=flat, repeated=2),
            )
        ),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )
    assert audit.survives_a_fresh_process is True


def test_the_three_conditions_on_clean_are_independently_observable() -> None:
    """**`clean` has three clauses and each needs a case where it is the only one
    failing**, or a test asserting `not clean` proves nothing about which clause did
    the work. The sabotage found all three at once: dropping any of them left every
    test passing, because every existing failure was overdetermined.

    Here the sweep is complete, nothing is detected, and the improvement exists on
    the repeated passes only — a warm-only gain too small to trip the caching check
    and still not a gain a real user gets.
    """
    original = {SECONDS: 100.0, QUERIES: 5.0, ROWS: 100.0, TOTAL: 100.0, SIZE: 2048.0}
    patched = dict(original)
    readings = pair(
        reading(Revision.ORIGINAL, first=original, repeated=3, warm={**original, SECONDS: 75.0}),
        reading(Revision.PATCHED, first=patched, repeated=3, warm={**patched, SECONDS: 64.0}),
    )
    readings[(Revision.ORIGINAL, Distribution.LONG_TAIL)] = reading(
        Revision.ORIGINAL, shape=Distribution.LONG_TAIL, first=original
    )
    readings[(Revision.PATCHED, Distribution.LONG_TAIL)] = reading(
        Revision.PATCHED, shape=Distribution.LONG_TAIL, first=patched
    )

    audit = detect(
        measuring(readings),
        metrics=EVERYTHING,
        shape=Distribution.UNIFORM,
        alternatives=[Distribution.LONG_TAIL],
    )
    assert audit.complete, "the first clause is satisfied"
    assert not audit.detected, "and the second"
    assert found(audit, Cheat.CACHED_STATE).finding is Finding.NOT_DETECTED
    assert audit.survives_a_fresh_process is False, "only the third fails"
    assert not audit.clean


def test_a_detected_cheat_alone_is_enough_to_fail_clean() -> None:
    """The second clause on its own: a complete sweep whose improvement survives cold,
    with one class found."""
    original = {SECONDS: 100.0, QUERIES: 101.0, ROWS: 100.0, TOTAL: 100.0, SIZE: 2048.0}
    patched = {SECONDS: 20.0, QUERIES: 2.0, ROWS: 50_000.0, TOTAL: 20.0, SIZE: 2048.0}
    readings = pair(
        reading(Revision.ORIGINAL, first=original, repeated=2),
        reading(Revision.PATCHED, first=patched, repeated=2),
    )
    readings[(Revision.ORIGINAL, Distribution.LONG_TAIL)] = reading(
        Revision.ORIGINAL, shape=Distribution.LONG_TAIL, first=original
    )
    readings[(Revision.PATCHED, Distribution.LONG_TAIL)] = reading(
        Revision.PATCHED, shape=Distribution.LONG_TAIL, first=patched
    )

    audit = detect(
        measuring(readings),
        metrics=EVERYTHING,
        shape=Distribution.UNIFORM,
        alternatives=[Distribution.LONG_TAIL],
    )
    assert audit.complete
    assert audit.survives_a_fresh_process is True
    assert [check.cheat for check in audit.detected] == [Cheat.OVER_FETCH]
    assert not audit.clean


def test_a_cold_pass_that_cost_nothing_has_no_warm_up_fraction() -> None:
    """A fraction of nothing is a division nobody can read, and reporting one would put
    an undefined number into an accusation."""
    free = Reading(
        revision=Revision.PATCHED,
        shape=Distribution.UNIFORM,
        first={SECONDS: 0.0},
        repeated=({SECONDS: 0.0},),
    )
    assert free.warm_up(SECONDS) is None


# ============ cached state, and the control that stops it accusing everything


def test_a_patch_that_warms_up_more_than_the_original_is_caching() -> None:
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0}, repeated=3, warm={SECONDS: 9.5})
    patched = reading(Revision.PATCHED, first={SECONDS: 10.0}, repeated=3, warm={SECONDS: 2.0})

    audit = detect(
        measuring(pair(original, patched)), metrics=COST_ONLY, shape=Distribution.UNIFORM
    )
    check = found(audit, Cheat.CACHED_STATE)
    assert check.finding is Finding.DETECTED
    assert check.numbers["excess"] > 0.12
    assert "state from one run into the next" in check.reason


def test_a_framework_that_warms_up_on_both_revisions_is_not_the_patch_cheating() -> None:
    """**The control, and without it this check accuses every patch ever measured.**
    Django fills a connection pool, compiles templates and populates an app registry
    on the first request through *any* codebase."""
    warms = {SECONDS: 2.0}
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0}, repeated=3, warm=warms)
    patched = reading(Revision.PATCHED, first={SECONDS: 10.0}, repeated=3, warm=warms)

    audit = detect(
        measuring(pair(original, patched)), metrics=COST_ONLY, shape=Distribution.UNIFORM
    )
    check = found(audit, Cheat.CACHED_STATE)
    assert check.finding is Finding.NOT_DETECTED
    assert "the framework doing what it does on any code" in check.reason


def test_a_warm_up_inside_the_noise_floor_is_not_reported() -> None:
    """Accusing a correct patch costs a repair cycle to discover the objection was
    noise — S-11.2's lesson, and the reason every borderline call errs this way."""
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0}, repeated=3, warm={SECONDS: 10.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 10.0}, repeated=3, warm={SECONDS: 9.0})

    audit = detect(
        measuring(pair(original, patched)), metrics=COST_ONLY, shape=Distribution.UNIFORM
    )
    assert found(audit, Cheat.CACHED_STATE).finding is Finding.NOT_DETECTED


def test_the_median_decides_the_warm_cost_not_one_slow_pass() -> None:
    """A garbage collection or a neighbour on the machine should not decide whether a
    patch is accused."""
    patched = Reading(
        revision=Revision.PATCHED,
        shape=Distribution.UNIFORM,
        first={SECONDS: 10.0},
        repeated=({SECONDS: 2.0}, {SECONDS: 99.0}, {SECONDS: 2.0}),
    )
    assert patched.warm(SECONDS) == 2.0
    assert patched.warm_up(SECONDS) == pytest.approx(0.8)


# ============ deferred work


def test_a_window_that_got_cheaper_while_the_process_did_not_is_deferred_work() -> None:
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0, TOTAL: 12.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 2.0, TOTAL: 12.0})

    audit = detect(
        measuring(pair(original, patched)),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, whole_process=TOTAL),
        shape=Distribution.UNIFORM,
    )
    check = found(audit, Cheat.DEFERRED_WORK)
    assert check.finding is Finding.DETECTED
    assert "left the measured window rather than the program" in check.reason


def test_work_that_left_the_process_as_well_is_not_deferred() -> None:
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0, TOTAL: 12.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 2.0, TOTAL: 4.0})

    audit = detect(
        measuring(pair(original, patched)),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, whole_process=TOTAL),
        shape=Distribution.UNIFORM,
    )
    assert found(audit, Cheat.DEFERRED_WORK).finding is Finding.NOT_DETECTED


def test_without_a_whole_process_metric_deferred_work_is_invisible() -> None:
    """Moving a cost outside the window improves the window. Nothing measured from
    inside it can say otherwise."""
    audit = detect(
        measuring(pair(reading(Revision.ORIGINAL), reading(Revision.PATCHED))),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )
    check = found(audit, Cheat.DEFERRED_WORK)
    assert check.finding is Finding.UNTESTED
    assert "invisible from inside it" in check.reason


# ============ over-fetch, which is the guard-counter non-negotiable


def test_queries_down_while_rows_explode_is_not_an_improvement() -> None:
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0, QUERIES: 101.0, ROWS: 100.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 4.0, QUERIES: 2.0, ROWS: 50_000.0})

    audit = detect(
        measuring(pair(original, patched)),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, calls=QUERIES, work=ROWS),
        shape=Distribution.UNIFORM,
    )
    check = found(audit, Cheat.OVER_FETCH)
    assert check.finding is Finding.DETECTED
    assert check.numbers == {
        "db.query_original": 101.0,
        "db.query_patched": 2.0,
        "rows_original": 100.0,
        "rows_patched": 50_000.0,
    }


def test_fewer_queries_returning_the_same_rows_is_a_real_fix() -> None:
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0, QUERIES: 101.0, ROWS: 100.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 4.0, QUERIES: 2.0, ROWS: 100.0})

    audit = detect(
        measuring(pair(original, patched)),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, calls=QUERIES, work=ROWS),
        shape=Distribution.UNIFORM,
    )
    assert found(audit, Cheat.OVER_FETCH).finding is Finding.NOT_DETECTED


def test_a_call_count_alone_cannot_answer_over_fetch() -> None:
    """One of the two numbers cannot say it. A harness counting only queries would
    report the improvement and never see what paid for it."""
    audit = detect(
        measuring(
            pair(
                reading(Revision.ORIGINAL, first={SECONDS: 10.0, QUERIES: 101.0}),
                reading(Revision.PATCHED, first={SECONDS: 4.0, QUERIES: 2.0}),
            )
        ),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, calls=QUERIES),
        shape=Distribution.UNIFORM,
    )
    check = found(audit, Cheat.OVER_FETCH)
    assert check.finding is Finding.UNTESTED
    assert "measure of work returned" in check.reason


def test_a_single_extra_row_counts_because_a_count_is_exact() -> None:
    """S-9.6's rule, not a threshold invented here: a count reproduces to the integer,
    so a count that moved at all moved."""
    original = reading(Revision.ORIGINAL, first={SECONDS: 10.0, QUERIES: 101.0, ROWS: 100.0})
    patched = reading(Revision.PATCHED, first={SECONDS: 4.0, QUERIES: 100.0, ROWS: 101.0})

    audit = detect(
        measuring(pair(original, patched)),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, calls=QUERIES, work=ROWS),
        shape=Distribution.UNIFORM,
    )
    assert found(audit, Cheat.OVER_FETCH).finding is Finding.DETECTED


# ============ stubbed response, where S-11.2 is the stronger answer


def test_an_equivalence_objection_settles_the_stubbed_response_class() -> None:
    """S-11.2 drives real payloads and comes back with a reproducing input. A size
    comparison is a proxy, and the proxy must not overrule the real comparison."""
    audit = detect(
        measuring(
            pair(
                reading(Revision.ORIGINAL, first={SECONDS: 10.0, SIZE: 2048.0}),
                reading(Revision.PATCHED, first={SECONDS: 4.0, SIZE: 2048.0}),
            )
        ),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, response_size=SIZE),
        shape=Distribution.UNIFORM,
        equivalence=an_equivalence(broken=True),
    )
    check = found(audit, Cheat.STUBBED_RESPONSE)
    assert check.finding is Finding.DETECTED
    assert "equivalence attack found 1 inputs" in check.reason


def test_a_smaller_response_is_the_weaker_signal_and_still_reported() -> None:
    audit = detect(
        measuring(
            pair(
                reading(Revision.ORIGINAL, first={SECONDS: 10.0, SIZE: 2048.0}),
                reading(Revision.PATCHED, first={SECONDS: 4.0, SIZE: 64.0}),
            )
        ),
        metrics=Metrics(cost=SECONDS, kinds=KINDS, response_size=SIZE),
        shape=Distribution.UNIFORM,
    )
    check = found(audit, Cheat.STUBBED_RESPONSE)
    assert check.finding is Finding.DETECTED
    assert "carries less than it did" in check.reason


def test_a_surviving_equivalence_attack_answers_the_class_without_a_size() -> None:
    audit = detect(
        measuring(pair(reading(Revision.ORIGINAL), reading(Revision.PATCHED))),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
        equivalence=an_equivalence(broken=False),
    )
    check = found(audit, Cheat.STUBBED_RESPONSE)
    assert check.finding is Finding.NOT_DETECTED
    assert "stronger answer than a size" in check.reason


def test_neither_a_size_nor_an_equivalence_leaves_the_response_unexamined() -> None:
    audit = detect(
        measuring(pair(reading(Revision.ORIGINAL), reading(Revision.PATCHED))),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )
    check = found(audit, Cheat.STUBBED_RESPONSE)
    assert check.finding is Finding.UNTESTED
    assert "what the response contains" in check.reason


# ============ shape-specific, which one fixture cannot answer


def test_an_improvement_that_holds_on_one_shape_only_is_a_special_case() -> None:
    original = {SECONDS: 10.0}
    readings = pair(
        reading(Revision.ORIGINAL, first=original),
        reading(Revision.PATCHED, first={SECONDS: 2.0}),
    )
    readings[(Revision.ORIGINAL, Distribution.LONG_TAIL)] = reading(
        Revision.ORIGINAL, shape=Distribution.LONG_TAIL, first=original
    )
    readings[(Revision.PATCHED, Distribution.LONG_TAIL)] = reading(
        Revision.PATCHED, shape=Distribution.LONG_TAIL, first=original
    )

    audit = detect(
        measuring(readings),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
        alternatives=[Distribution.LONG_TAIL],
    )
    check = found(audit, Cheat.SHAPE_SPECIFIC)
    assert check.finding is Finding.DETECTED
    assert Distribution.LONG_TAIL.value in check.reason


def test_one_fixture_shape_cannot_answer_the_shape_specific_class() -> None:
    """**S-9.3's argument, arriving two epics later at the artifact it warned about.**
    A special case for the seeded shape looks exactly like a general fix when
    measured on that shape."""
    audit = detect(
        measuring(pair(reading(Revision.ORIGINAL), reading(Revision.PATCHED))),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
    )
    check = found(audit, Cheat.SHAPE_SPECIFIC)
    assert check.finding is Finding.UNTESTED
    assert "invisible from that shape" in check.reason


def test_an_improvement_holding_everywhere_clears_the_class() -> None:
    readings = pair(
        reading(Revision.ORIGINAL, first={SECONDS: 10.0}),
        reading(Revision.PATCHED, first={SECONDS: 2.0}),
    )
    readings[(Revision.ORIGINAL, Distribution.POWER_LAW)] = reading(
        Revision.ORIGINAL, shape=Distribution.POWER_LAW, first={SECONDS: 20.0}
    )
    readings[(Revision.PATCHED, Distribution.POWER_LAW)] = reading(
        Revision.PATCHED, shape=Distribution.POWER_LAW, first={SECONDS: 5.0}
    )

    audit = detect(
        measuring(readings),
        metrics=COST_ONLY,
        shape=Distribution.UNIFORM,
        alternatives=[Distribution.POWER_LAW],
    )
    assert found(audit, Cheat.SHAPE_SPECIFIC).finding is Finding.NOT_DETECTED


# ============ the harness has to hand back what it was asked for


def test_a_measure_that_ignores_its_arguments_is_refused() -> None:
    """**The failure this catches is a patch cleared by a measurement that never
    distinguished it from the original.** A harness returning the same reading for
    both revisions would make every class come back absent."""
    same = reading(Revision.ORIGINAL)

    def careless(revision: Revision, shape: Distribution) -> Reading:
        return same

    with pytest.raises(CheatError, match="not of what was requested"):
        detect(careless, metrics=COST_ONLY, shape=Distribution.UNIFORM)


def test_a_reading_from_the_wrong_shape_is_refused() -> None:
    readings = pair(
        reading(Revision.ORIGINAL),
        reading(Revision.PATCHED, shape=Distribution.LONG_TAIL),
    )

    def confused(revision: Revision, shape: Distribution) -> Reading:
        return readings[(revision, Distribution.UNIFORM)]

    with pytest.raises(CheatError, match="not of what was requested"):
        detect(confused, metrics=COST_ONLY, shape=Distribution.UNIFORM)


def test_a_reading_that_measured_nothing_is_refused() -> None:
    with pytest.raises(CheatError, match="report that as the patch being honest"):
        Reading(revision=Revision.PATCHED, shape=Distribution.UNIFORM, first={})


def test_a_metric_with_no_declared_kind_is_refused_at_the_top() -> None:
    """The two rules disagree about every small move, so a metric compared under
    whichever default happened to be there is compared under the wrong one.

    **This test found a real hole by expecting the wrong thing.** It expected
    `detect` to raise; `detect` succeeded. With the check only inside `kind_of`, a
    cost metric with no declared kind produced a *complete* audit — every class
    `UNTESTED` for want of other metrics, so nothing ever asked what rule the cost
    metric moved under — and the error waited to surface from a property, on an
    artifact a verdict could already read.
    """
    with pytest.raises(CheatError, match="no rule for what a move in them means"):
        Metrics(cost=SECONDS, kinds={QUERIES: MetricKind.COUNT})

    with pytest.raises(CheatError, match=r"\['rows'\]"):
        Metrics(cost=SECONDS, kinds={SECONDS: MetricKind.DURATION}, work=ROWS)
