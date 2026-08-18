"""Two percent apart, invisible to a clock, and told apart to the integer.

S-3.19. The backlog note is the specification: S-0.4 measured the timing noise
floor at ~20ms, about 6% of a 350ms endpoint, at 20 repetitions — so a real 2%
improvement is invisible to timing however many times it is run. The test that
matters here does exactly that. Two loops differing by 2%, timed with S-1.6's
interleaved comparison and separated to the instruction.

The second test that matters is the blind one. `sorted()` retires 23 bytecode
instructions on a thousand elements because the sort happens in C, and a smaller
count that means *the instrument could not see the work* is the silent wrong
answer this project is built to refuse.

Counts are asserted **exactly equal**, including across a fresh process with a
different hash seed. The stated reproducibility tolerance is zero; anything else
would give away the only reason to have this instrument.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from functools import partial
from pathlib import Path
from statistics import median

import pytest

from coldfix.bench.interleaving import compare
from coldfix.primitives.instructions import (
    VISIBLE_INSTRUCTIONS_PER_SECOND,
    CounterUnavailableError,
    InstructionCount,
    Separation,
    counting_instructions,
    measure_instructions,
    separate,
)
from coldfix.primitives.measurement import INSTRUCTIONS, MATERIALIZED
from coldfix.primitives.registry import REGISTRY, Capability, ProjectProfile

# S-0.4's measured noise floor, and the reason this primitive exists. A wall
# clock difference smaller than this is not reportable however many samples are
# taken — S-3.8's envelope enforces the same rule on candidates.
TIMING_FLOOR_SECONDS = 0.020


def summing_loop(n: int) -> int:
    """The subject. Retires a fixed number of instructions per iteration."""
    total = 0
    for i in range(n):
        total += i
    return total


def sorts_in_c(n: int) -> list[int]:
    """Everything this does happens below the interpreter."""
    return sorted(range(n, 0, -1))


def sleeps(seconds: float) -> None:
    """Blocked, not computing. §12 gives this to S-3.7 rather than to this."""
    time.sleep(seconds)


def a_count(instructions: int, reference_seconds: float = 0.01) -> InstructionCount:
    return InstructionCount(
        instructions=instructions,
        drain_instructions=0,
        materialized=1,
        reference_seconds=reference_seconds,
    )


# ------------------------------------------- AC 1 and 2: exact and reproducible


def test_the_same_workload_retires_the_same_count_every_time() -> None:
    """AC 2, and the stated tolerance is zero. Equal to the integer or the
    instrument is broken."""
    counts = {measure_instructions(lambda: summing_loop(100)).instructions for _ in range(5)}

    assert len(counts) == 1


def test_the_count_is_exactly_linear_in_the_work_done() -> None:
    """AC 1. The number is of real executed bytecode rather than of anything
    incidental, and it is the same shape as the work.

    Asserted as equal *differences* rather than as `24 + 7n`, because the
    constants belong to a CPython version while the linearity belongs to the
    instrument.
    """
    at = {n: measure_instructions(partial(summing_loop, n)).instructions for n in (100, 200, 300)}

    assert at[200] - at[100] == at[300] - at[200]
    assert at[200] - at[100] > 0


def test_the_count_survives_a_fresh_process_and_a_different_hash_seed() -> None:
    """AC 1's "independent of machine and load", tested where it could actually
    fail: a new interpreter, with string hashing randomised differently, which is
    what changes set iteration order and therefore which branches run."""
    script = (
        "from coldfix.primitives.instructions import measure_instructions\n"
        "def loop():\n"
        "    total = 0\n"
        "    for i in range(500):\n"
        "        total += i\n"
        "    return total\n"
        "print(measure_instructions(loop).instructions)\n"
    )
    counts = {_run_in_subprocess(script, hash_seed=seed) for seed in ("1", "2", "random")}

    assert len(counts) == 1


def _run_in_subprocess(script: str, *, hash_seed: str) -> str:
    environment = dict(os.environ, PYTHONHASHSEED=hash_seed)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
        cwd=Path(__file__).parents[2],
    )
    return completed.stdout.strip()


def test_the_counter_sees_a_lazy_result_only_because_it_is_drained_inside() -> None:
    """The window closes after the result is materialized, which is the same rule
    `measure_once` follows — a generator that is returned has done nothing."""
    counted = measure_instructions(lambda: (x * 2 for x in range(200)))

    assert counted.materialized == 200
    assert counted.instructions > 200


def test_the_harness_drain_is_subtracted_and_recorded() -> None:
    """The correction that makes the guard below work at all.

    Counted naively, `sorted(range(50_000, 0, -1))` retires 300,096 instructions
    for a sort that happens entirely in C — every one of them the harness's own
    loop reading the result. The same drain over a filler of the same length is
    300,087, so the subject's share is nine.
    """
    counted = measure_instructions(lambda: sorts_in_c(50_000))

    assert counted.drain_instructions > 100_000
    assert counted.instructions < 1_000
    assert counted.materialized == 50_000


def test_a_lazy_result_keeps_its_own_body_after_the_drain_is_subtracted() -> None:
    """The correction removes the harness's loop, not the subject's work. A
    generator expression's body runs during iteration and stays counted."""
    lazy = measure_instructions(lambda: (x * 2 for x in range(200)))
    already = measure_instructions(lambda: [x * 2 for x in range(200)])

    assert lazy.instructions > 200
    assert lazy.instructions > already.instructions - lazy.drain_instructions


def test_the_first_run_of_a_result_type_does_not_inflate_the_count() -> None:
    """Measured: the same workload counted 1311 then 787, 787, 787, 787.

    `isinstance(result, Iterable)` runs `__subclasshook__` — Python code — once
    per result type and caches it forever. Uncounted, that one-time work lands on
    whichever variant of a comparison ran first, which biases every comparison in
    favour of the second. The type here is fresh, so a missing warm-up shows up
    as a first count several times the others.
    """

    class NeverSeenBefore:
        def __init__(self, n: int) -> None:
            self.total = sum(range(n))

    counts = [measure_instructions(lambda: NeverSeenBefore(50)).instructions for _ in range(4)]

    assert len(set(counts)) == 1


def test_the_context_manager_stops_counting_when_the_block_ends() -> None:
    with counting_instructions() as tally:
        summing_loop(50)
    after = tally.count

    summing_loop(50)

    assert tally.count == after


def test_two_counts_cannot_be_nested() -> None:
    """One tool cannot hold two independent tallies, and the second attempt says
    so rather than silently sharing the first one's counter."""
    with counting_instructions(), counting_instructions():  # noqa: SIM117 - the nesting is it
        with pytest.raises(CounterUnavailableError, match="no monitoring tool id is free"):
            with counting_instructions():
                pass


def test_the_tool_id_is_released_afterwards() -> None:
    """A leaked tool id would make every later count in the process fail."""
    for _ in range(3):
        with counting_instructions():
            pass

    assert measure_instructions(lambda: summing_loop(10)).instructions > 0


# ---------------------------- AC 3: a metric in the same vocabulary as the rest


def test_the_count_is_offered_as_a_metric_mapping() -> None:
    """AC 3. Shaped so S-3.18's `screen` can compare it against an instruction
    floor from a reference implementation."""
    counted = measure_instructions(lambda: summing_loop(100))

    assert counted.metrics[INSTRUCTIONS] == counted.instructions
    assert counted.metrics[MATERIALIZED] == counted.materialized


def test_an_instrumented_run_reports_no_duration_at_all() -> None:
    """Not a documented caveat, a missing key. Counting costs about 33x the run,
    so a `seconds` measured under it is a number about the instrument — and one
    present in the mapping would eventually be compared against a clean one."""
    counted = measure_instructions(lambda: summing_loop(100))

    assert "seconds" not in counted.metrics
    assert "cpu_seconds" not in counted.metrics
    assert "blocked_seconds" not in counted.metrics


# ------------------- AC 4: separable below the floor that hides the difference


def test_two_percent_apart_is_invisible_to_timing_and_exact_here() -> None:
    """AC 4, and the whole reason this primitive exists.

    The two loops differ by 2%. Timed with S-1.6's interleaved comparison the
    difference lands far below S-0.4's ~20ms floor, so it is not reportable
    however significant a rank test finds it — S-3.8 enforces exactly that rule
    on candidates. Counted, they differ exactly and reproducibly.
    """
    baseline = 10_000
    improved = 9_800

    timed = compare(
        lambda: summing_loop(baseline),
        lambda: summing_loop(improved),
        n=20,
        label_a="baseline",
        label_b="2% fewer iterations",
        seed=11,
    )
    gap = abs(median(timed.run_a.durations) - median(timed.run_b.durations))

    counted = separate(
        lambda: summing_loop(baseline),
        lambda: summing_loop(improved),
        label_a="baseline",
        label_b="2% fewer iterations",
    )

    assert gap < TIMING_FLOOR_SECONDS
    assert counted.separated
    assert counted.cheaper == "2% fewer iterations"
    assert counted.ratio == pytest.approx(0.98, abs=0.005)


def test_the_separation_records_the_metric_the_conclusion_rests_on() -> None:
    """AC 5. Not decoration: this object exists because the timing difference
    between its subjects may be unreportable, so a reader has to know which
    number decided."""
    counted = separate(lambda: summing_loop(100), lambda: summing_loop(200))

    assert counted.decided_by == INSTRUCTIONS
    assert "rests on instructions" in counted.explanation()


def test_the_separation_says_it_is_a_search_result_and_not_a_verified_one() -> None:
    """§12's workflow in full: search against instruction count, *then* validate
    the single winner with interleaved statistical timing. A count that read as a
    verified improvement would skip the half that measures speed."""
    explanation = separate(lambda: summing_loop(100), lambda: summing_loop(200)).explanation()

    assert "search result, not a verified improvement" in explanation
    assert "interleaved statistical timing" in explanation


def test_identical_implementations_are_reported_as_identical() -> None:
    """And identical here means identical, because the metric reproduces to the
    integer. There is no difference too small for it to have found."""
    counted = separate(lambda: summing_loop(100), lambda: summing_loop(100))

    assert not counted.separated
    assert counted.cheaper is None
    assert "no difference too small" in counted.explanation()


def test_counts_measured_earlier_are_refused() -> None:
    """S-1.6's rule. A comparison that accepted a stored number would give up the
    one property that makes this metric worth having."""
    with pytest.raises(TypeError, match="stored baseline"):
        separate(1_000, lambda: summing_loop(100))  # type: ignore[arg-type]


# ------------------------------- the guard: a count of work it could not see


def test_work_done_in_c_is_reported_as_hidden_rather_than_as_cheap() -> None:
    """`sorted()` retires 23 instructions on a thousand elements. A smaller count
    that means *the instrument could not see it* is the exact silent wrong answer
    the guard-counter rule exists for."""
    counted = measure_instructions(lambda: sorts_in_c(50_000))

    assert counted.hidden_work
    assert "cannot see" in counted.explanation()
    assert "off-CPU split" in counted.explanation()


def test_time_spent_blocked_is_reported_as_hidden_too() -> None:
    """§12 gives I/O and lock waiting to S-3.7, and this says so rather than
    reporting a sleeping workload as nearly free."""
    counted = measure_instructions(lambda: sleeps(0.05))

    assert counted.hidden_work
    assert (counted.instructions_per_second or 0) < VISIBLE_INSTRUCTIONS_PER_SECOND


def test_a_workload_the_interpreter_actually_runs_is_not_flagged() -> None:
    """The control. A guard that fired on everything would be switched off, and
    then the blind measurements would go through as well."""
    counted = measure_instructions(lambda: summing_loop(50_000))

    assert not counted.hidden_work
    assert (counted.instructions_per_second or 0) > VISIBLE_INSTRUCTIONS_PER_SECOND


def test_comparing_two_workloads_the_instrument_cannot_see_refuses_to_conclude() -> None:
    """Both counts are exact and neither is about speed. Reporting the smaller
    one as cheaper is how this instrument would produce a confident wrong fix."""
    counted = separate(
        lambda: sorts_in_c(50_000),
        lambda: sorts_in_c(60_000),
        label_a="fifty thousand",
        label_b="sixty thousand",
    )

    assert not counted.trustworthy
    assert "Neither number is a statement about speed" in counted.explanation()
    assert counted.a.instructions < 100  # the sort itself is invisible


def test_a_run_too_short_to_time_leaves_visibility_unknown() -> None:
    """`None` rather than a rate computed against rounding, and unknown does not
    become hidden — a guard that fired on an unmeasurable reference would flag
    every fast workload."""
    counted = a_count(instructions=5, reference_seconds=0.0)

    assert counted.instructions_per_second is None
    assert not counted.hidden_work


# ------------------------------------------------------------- the registration


def test_the_primitive_needs_the_instruction_counting_capability() -> None:
    primitive = REGISTRY.get("observation.instructions")

    assert primitive.required_capabilities == frozenset({Capability.INSTRUCTION_COUNTING})


def test_the_primitive_is_withheld_where_the_capability_is_absent() -> None:
    selection = REGISTRY.select(ProjectProfile(capabilities=frozenset()))

    assert "observation.instructions" not in selection.names


def test_a_ratio_against_a_workload_that_retired_nothing_is_none() -> None:
    separation = Separation(
        label_a="nothing",
        label_b="something",
        a=a_count(instructions=0),
        b=a_count(instructions=100),
    )

    assert separation.ratio is None
    assert separation.separated
    assert separation.cheaper == "nothing"
