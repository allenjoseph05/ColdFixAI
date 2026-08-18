"""`compare()` cancels drift, and cannot be handed a stored baseline.

The load-bearing test here is `test_interleaving_cancels_a_drifting_machine`,
paired with `test_a_block_design_manufactures_a_difference_on_the_same_work`.
Together they are the adversarial shape `CLAUDE.md` asks for: the second runs
the *same* workload under the ordering this module refuses to use and shows it
produces a confident, entirely fictional difference. Without that pair, the
first test only proves that two equal things measured equally look equal.

Durations are produced by busy-waiting on `perf_counter` rather than by
`sleep`, because sleep resolution is coarse enough on Windows to swamp the
effects being tested, and because a busy wait is what an actual workload looks
like to the clock.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

import pytest

from coldfix.bench.interleaving import (
    ComparisonError,
    InterleavedComparison,
    compare,
)
from coldfix.bench.stats import rank_test
from coldfix.bench.timing import ProcessState

ALPHA = 0.05


def busy(seconds: float) -> None:
    """Occupy the clock for `seconds`, accurately enough to test against."""
    end = perf_counter() + seconds
    while perf_counter() < end:
        pass


def drifting_workload(base: float, step: float) -> Callable[[], None]:
    """One workload whose cost grows with every call, whoever makes it.

    A machine that gets slower as a session runs — cache pressure, thermal
    throttling, a neighbour warming up. Both variants share this single
    function and this single counter, so any difference the statistics find
    between them is a difference the *ordering* invented.
    """
    calls = 0

    def work() -> None:
        nonlocal calls
        calls += 1
        busy(base + calls * step)

    return work


# ------------------------------------------------- a stored baseline cannot get in


def test_measurements_taken_earlier_are_refused() -> None:
    """The AC that has to be structural rather than conventional.

    A stored baseline is a list of numbers. There is no parameter it fits, and
    the error says why rather than surfacing as "'list' object is not callable"
    from three frames down.
    """
    stored = [0.010, 0.011, 0.009, 0.012, 0.010, 0.011, 0.010, 0.013]

    with pytest.raises(TypeError, match="cannot accept measurements taken earlier"):
        compare(stored, lambda: busy(0.001), 8)  # type: ignore[arg-type]


def test_both_variants_are_checked_not_only_the_first() -> None:
    with pytest.raises(TypeError, match="variant 'b'"):
        compare(lambda: busy(0.001), [0.01] * 8, 8)  # type: ignore[arg-type]


# --------------------------------------------------------------- the schedule


def run_equal_pair(n: int = 8, seed: int | None = None) -> InterleavedComparison:
    return compare(lambda: busy(0.001), lambda: busy(0.001), n, seed=seed)


def test_every_round_runs_both_variants_once() -> None:
    """Alternation: balanced across every prefix, so drift lands on both.

    This is the property a single shuffle of the whole session gives up — it
    can deal one condition into the first half by chance, which is the block
    design interleaving exists to replace.
    """
    result = run_equal_pair(n=16)

    assert len(result.order) == 32
    rounds = [result.order[i : i + 2] for i in range(0, 32, 2)]
    assert all(set(pair) == {"a", "b"} for pair in rounds), result.order


def test_the_order_within_a_round_is_randomized() -> None:
    """Balanced is not enough — a fixed phase is something drift can lock onto."""
    firsts = {compare(lambda: None, lambda: None, 8, seed=s).order[0] for s in range(20)}
    orders = {compare(lambda: None, lambda: None, 8, seed=s).order for s in range(20)}

    assert firsts == {"a", "b"}, "the leading variant never changed"
    assert len(orders) > 1, "every session produced the same schedule"


def test_a_recorded_seed_reproduces_the_order() -> None:
    """An experiment that cannot be re-run in its original order is not reproducible."""
    first = run_equal_pair(n=8)
    again = run_equal_pair(n=8, seed=first.seed)

    assert again.order == first.order
    assert again.seed == first.seed


def test_a_seed_is_recorded_even_when_the_caller_supplies_none() -> None:
    assert isinstance(run_equal_pair().seed, int)


# ---------------------------------------------------------------- what is returned


def test_returns_both_distributions_and_the_rank_test() -> None:
    result = run_equal_pair(n=10)

    assert len(result.run_a) == 10
    assert len(result.run_b) == 10
    assert result.rank.n_a == 10
    assert result.rank.n_b == 10
    assert 0.0 <= result.rank.p_value <= 1.0


def test_sample_indices_are_positions_in_the_session() -> None:
    """Plotting duration against this index is how drift becomes visible.

    A per-variant index would renumber both runs 0..n-1 and lose the ordering
    that the whole operation is about.
    """
    result = run_equal_pair(n=8)

    positions = sorted(sample.index for sample in (*result.run_a.samples, *result.run_b.samples))
    assert positions == list(range(16))


def test_only_the_first_sample_of_a_session_is_fresh() -> None:
    """`FRESH` is scoped to the run, and here the run is the whole session."""
    result = run_equal_pair(n=8)

    everything = sorted(
        (*result.run_a.samples, *result.run_b.samples), key=lambda sample: sample.index
    )
    assert everything[0].process_state is ProcessState.FRESH
    assert all(sample.process_state is ProcessState.REUSED for sample in everything[1:])


def test_nothing_is_discarded() -> None:
    """S-1.2 refuses to drop a warmup sample, and composing must not reintroduce it."""
    result = compare(lambda: None, lambda: None, 30)

    assert len(result.run_a) == 30
    assert len(result.run_b) == 30


# --------------------------------------------- the property the story exists for


def test_interleaving_cancels_a_drifting_machine() -> None:
    """A known-equal pair on a machine that slows down must still read as equal.

    Both variants are the *same function* sharing one counter, so there is no
    real difference to find. Every seed must agree, because a verdict that
    depends on the shuffle is a verdict the shuffle produced.
    """
    for seed in range(12):
        work = drifting_workload(base=0.001, step=0.0004)
        result = compare(work, work, 12, seed=seed)

        assert result.rank.p_value > ALPHA, (
            f"seed {seed} found a difference between a function and itself: "
            f"p={result.rank.p_value:.4f}"
        )


def test_a_block_design_manufactures_a_difference_on_the_same_work() -> None:
    """The adversarial half: run the same workload the way this module refuses to.

    All of one condition, then all of the other — the shape of comparing
    against a baseline stored earlier. Nothing about the workload changed, and
    the drift alone is now a decisive result. This is the false positive
    Laaber et al. measured, reproduced in a few lines.
    """
    work = drifting_workload(base=0.001, step=0.0004)
    first_block = [_timed(work) for _ in range(12)]
    second_block = [_timed(work) for _ in range(12)]

    verdict = rank_test(first_block, second_block)

    assert verdict.p_value < 0.001, "the block design was expected to find a difference"
    assert verdict.effect == 0.0, "every sample in the second block should be slower"


def test_a_real_difference_survives_the_interleaving() -> None:
    """Cancelling drift must not also cancel signal.

    The complement of the drift test: if randomized ordering made everything
    look equal it would satisfy that test perfectly and be useless.
    """
    for seed in range(5):
        result = compare(lambda: busy(0.001), lambda: busy(0.004), 10, seed=seed)

        assert result.rank.p_value < ALPHA, f"seed {seed} missed a fourfold difference"
        assert result.rank.effect < 0.5, "a should be measured as the faster variant"


def _timed(fn: Callable[[], None]) -> float:
    started = perf_counter()
    fn()
    return perf_counter() - started


# ------------------------------------------------------------------- refusals


def test_a_group_smaller_than_the_rank_test_accepts_is_refused_before_running() -> None:
    """Discovering this after taking every sample would waste the whole session."""
    calls = 0

    def counted() -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(ValueError, match="at least 8 per variant"):
        compare(counted, counted, 7)

    assert calls == 0, "samples were taken before the refusal"


def test_a_variant_that_raises_reports_where_it_happened() -> None:
    """Failing on round 1 and failing on round 19 are different findings."""
    rounds_run = 0

    def fails_late() -> None:
        nonlocal rounds_run
        rounds_run += 1
        if rounds_run > 5:
            message = "workload degraded"
            raise RuntimeError(message)

    with pytest.raises(ComparisonError) as caught:
        compare(fails_late, lambda: None, 10, seed=1)

    assert caught.value.round_index > 0
    assert caught.value.rounds == 10
    assert isinstance(caught.value.__cause__, RuntimeError)
