"""The benchmark runner, and the number it refuses to produce.

S-15.2. Two constraints do most of the work here and both come from the project's
own reading of the literature:

- **`05-research.md` §10.4** — a 2026 audit found that runtime measurements in
  GSO, SWE-Perf and SWE-fficiency are not fixed quantities, *despite* those
  benchmarks already using repeated trials, outlier filtering and statistical
  tests. So an uncertified speedup is recorded as unresolved, not as a win or a
  loss.
- **AC 3** — per category, never aggregate. There is no total, and the tests
  assert that there is no way to get one.
"""

from __future__ import annotations

import inspect

import pytest

from coldfix.bench.certification import Certification
from coldfix.eval.benchmark import (
    Benchmark,
    BenchmarkError,
    Category,
    Instance,
    Result,
    Standing,
    benchmark,
    standings,
)


def certification(*, certified: bool = True) -> Certification:
    """A certification with the one field this module reads, and real numbers
    behind it so the artifact is the shape the bench produces."""
    return Certification(
        workload="tickets",
        n=30,
        samples=(0.10, 0.11, 0.12),
        mean_seconds=0.11,
        median_seconds=0.11,
        stdev_seconds=0.01,
        coefficient_of_variation=0.09,
        minimum_detectable_effect=0.03 if certified else 0.40,
        target_effect=0.05,
        certified=certified,
        refusal=None if certified else "the floor is 40% and the target is 5%",
        seed=7,
    )


def instance(
    identifier: str = "swe-perf-1", *, category: str = "orm", expert: float = 2.0
) -> Instance:
    return Instance(instance_id=identifier, category=category, expert_speedup=expert)


def result(
    identifier: str = "swe-perf-1",
    *,
    speedup: float | None = 2.5,
    category: str = "orm",
    expert: float = 2.0,
    certified: bool = True,
) -> Result:
    return Result(
        instance=instance(identifier, category=category, expert=expert),
        speedup=speedup,
        certification=certification(certified=certified),
    )


def a_run(*results: Result) -> Benchmark:
    return benchmark(
        corpus="SWE-Perf",
        corpus_size=140,
        selection="every instance whose subject is a Django project on Postgres",
        results=list(results),
    )


# ============================================== an uncertified result is neither


def test_an_uncertified_speedup_is_unresolved_rather_than_a_win() -> None:
    """**§10.4's requirement, and it decides before the numbers are compared.**

    A speedup the harness could not resolve is not a small speedup — it is an
    unread instrument. Comparing it against the expert's figure would be
    arithmetic over a number nobody measured.
    """
    generous = result(speedup=9.0, expert=2.0, certified=False)

    assert generous.standing is Standing.UNRESOLVED


def test_an_uncertified_failure_is_unresolved_too() -> None:
    """The refusal is symmetric, which is what makes it about the instrument.

    A rule that only discounted the wins would be a rule about optimism rather
    than about measurement.
    """
    flat = result(speedup=1.0, certified=False)

    assert flat.standing is Standing.UNRESOLVED


def test_unresolved_instances_are_excluded_from_the_rate_not_counted_against() -> None:
    """**The arithmetic that would make a noisy machine look like a bad system.**

    Counting them as failures says *we tried and did not match*, which is a claim
    about the patch. What happened is that nothing was measured.
    """
    category = Category(
        name="orm",
        results=(
            result("a", speedup=2.5),
            result("b", speedup=2.5),
            result("c", certified=False),
        ),
    )

    assert category.resolved == 2
    assert category.matched_rate == pytest.approx(1.0)
    assert "excluded from the rate" in category.describe()


def test_a_category_where_nothing_resolved_has_no_rate() -> None:
    """`None`, not zero. The instances were attempted; the ratio is undefined."""
    category = Category(name="orm", results=(result("a", certified=False),))

    assert category.matched_rate is None
    assert "no rate to report" in category.describe()


# ================================================= comparing against the expert


def test_meeting_the_expert_speedup_is_a_match() -> None:
    assert result(speedup=2.0, expert=2.0).standing is Standing.MATCHED
    assert result(speedup=2.5, expert=2.0).standing is Standing.MATCHED


def test_improving_by_less_than_the_expert_is_short_rather_than_a_failure() -> None:
    """A real improvement that did not reach the expert's is still an improvement.

    Folding it into *no change* would lose the distinction the whole comparison
    exists to make.
    """
    assert result(speedup=1.4, expert=2.0).standing is Standing.SHORT


def test_no_patch_and_no_improvement_are_both_no_change_but_not_the_same_field() -> None:
    """`speedup=None` is *nothing was proposed*; 1.0 is *something changed nothing*.

    They share a standing because neither made the program faster, and they are
    kept apart on the artifact because a benchmark that recorded them alike would
    credit a system that never answered with a null improvement.
    """
    silent = result(speedup=None)
    tried = result(speedup=1.0)

    assert silent.standing is Standing.NO_CHANGE
    assert tried.standing is Standing.NO_CHANGE
    assert silent.speedup is None
    assert tried.speedup == 1.0
    assert "no patch" in silent.describe()


# =========================================== per category, and never as one number


def test_results_are_grouped_by_the_datasets_own_categories() -> None:
    """The categories come from SWE-Perf. This project has no taxonomy of its own."""
    run = a_run(
        result("a", category="orm"),
        result("b", category="serialization"),
        result("c", category="orm"),
    )

    assert [category.name for category in run.categories] == ["orm", "serialization"]
    assert len(run.categories[0].results) == 2


def test_there_is_no_aggregate_and_no_way_to_produce_one() -> None:
    """AC 3, asserted as an absence.

    A single rate over unlike instances averages measurements taken on different
    scales — `08-audit.md`'s argument about ranking across kinds, applied to
    scoring across categories. So there is no total on the artifact, and a test
    that only checked the render would pass for one that had a property nobody
    printed.
    """
    run = a_run(result("a", category="orm"), result("b", category="serialization"))

    public = {name for name in dir(run) if not name.startswith("_")}

    assert not {"rate", "total", "matched_rate", "overall", "score"} & public
    assert "categor" in run.render()


def test_the_subset_and_its_corpus_are_both_stated() -> None:
    """AC 4. A subset is only stated openly if a reader sees what it is a subset of."""
    rendered = a_run(result("a")).render()

    assert "1 of 140 instance(s)" in rendered
    assert "Django project on Postgres" in rendered


# ====================================================== what the run refuses


def test_an_unstated_selection_is_refused() -> None:
    """A subset whose criteria are unstated cannot be told from one chosen after
    the numbers were seen."""
    with pytest.raises(BenchmarkError, match="stated openly"):
        benchmark(corpus="SWE-Perf", corpus_size=140, selection="  ", results=[result()])


def test_a_run_over_no_instances_is_refused() -> None:
    """Nothing attempted and nothing achieved are different results."""
    with pytest.raises(BenchmarkError, match="no instances"):
        benchmark(corpus="SWE-Perf", corpus_size=140, selection="all of them", results=[])


def test_a_subset_larger_than_its_corpus_is_refused() -> None:
    """One of the two numbers is wrong, and every rate below becomes unreadable."""
    with pytest.raises(BenchmarkError, match="larger than its corpus"):
        benchmark(
            corpus="SWE-Perf",
            corpus_size=1,
            selection="two of them",
            results=[result("a"), result("b")],
        )


def test_an_instance_scored_twice_is_refused() -> None:
    """It moves the rate for a reason that is not about the system."""
    with pytest.raises(BenchmarkError, match="appears twice"):
        a_run(result("a"), result("a"))


def test_an_expert_patch_that_did_not_speed_anything_up_is_refused() -> None:
    """There is nothing to score against.

    A ratio of 1.0 would make every attempt a match, and one below it would make
    doing nothing a win.
    """
    with pytest.raises(BenchmarkError, match="expert speedup"):
        instance(expert=1.0)


def test_an_instance_without_a_category_is_refused() -> None:
    """AC 3 reports per category, so an uncategorised instance has nowhere to go."""
    with pytest.raises(BenchmarkError, match="no category"):
        instance(category="   ")


# ======================================================= the harness runs nothing


def test_the_runner_takes_recorded_results_and_drives_nothing() -> None:
    """`CLAUDE.md`: a study does not take its own measurements.

    Asserted by construction — every input is a value, and there is no parameter
    through which a pipeline, a session or a client could be handed to it.
    """
    parameters = set(inspect.signature(benchmark).parameters)

    assert parameters == {"corpus", "corpus_size", "selection", "results"}


def test_standings_counts_without_deciding_anything() -> None:
    """For a caller reporting elsewhere — the failure catalogue, for one."""
    counted = standings([result("a"), result("b", speedup=1.4), result("c", certified=False)])

    assert counted[Standing.MATCHED] == 1
    assert counted[Standing.SHORT] == 1
    assert counted[Standing.UNRESOLVED] == 1
