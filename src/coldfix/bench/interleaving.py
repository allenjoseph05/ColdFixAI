"""Compare two variants in one session, in randomized interleaved order.

The sixth operation of the lab bench, and the first one that composes the others
rather than reaching the machine itself. It takes samples with `time()` and
reaches a comparison with `rank_test()`; what it owns is the *order the samples
are taken in*, which is the part that decides whether the comparison means
anything.

**The failure this exists to prevent is comparing against a stored baseline.**
Laaber et al. ran 4.5 million microbenchmark data points across three clouds and
found that naive mean comparison against a previously recorded number produces
high false-positive rates — reporting a change when neither the benchmark nor
the code changed. What works is running both conditions on the same instance in
randomized interleaved order; with that, slowdowns of 10% and under become
detectable with a rank test (`05-research.md` §10.3).

That is not advice this module follows. It is a shape the signature enforces:
`compare()` takes two **callables** and runs both itself. A stored measurement
is a list of numbers, and there is no parameter here a list of numbers fits.
The dangerous call is not discouraged, it is unrepresentable — the same
construction as `execute()` making `timeout` required.

**Randomized within each round, not shuffled across the session.** The story
asks for both alternation and randomization, and a single shuffle of `n` A's and
`n` B's gives up the first: it can deal all the A's into the first half by
chance, which is the block design interleaving exists to replace. Drawing a
fresh order for each pair keeps both conditions balanced across every prefix of
the session — so a machine that drifts monotonically drifts under both equally —
while leaving no fixed phase for a periodic disturbance to lock onto.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from coldfix.bench.stats import MINIMUM_GROUP_SIZE, RankTest, rank_test
from coldfix.bench.timing import ProcessState, Sample, TimingError, TimingRun, time

# The seed is drawn from this range when the caller does not supply one, and is
# recorded on the result. An experiment that cannot be re-run in the order it
# originally ran in is not reproducible, and the append-only experiment log has
# to be able to say what actually happened.
_SEED_BITS = 63


class ComparisonError(Exception):
    """A variant raised, so the session is incomplete.

    Carries where in the schedule it happened. A variant that fails on round 1
    and one that fails on round 19 are different findings: the second says the
    workload degrades under repetition, which is itself the kind of thing this
    project exists to detect.
    """

    def __init__(self, label: str, round_index: int, rounds: int) -> None:
        self.label = label
        self.round_index = round_index
        self.rounds = rounds
        super().__init__(
            f"variant {label!r} raised on round {round_index} of {rounds}; "
            "the comparison is incomplete"
        )


@dataclass(frozen=True)
class InterleavedComparison:
    """Two distributions taken in one session, and the test between them.

    There is no verdict field, and that is deliberate. `rank.p_value` says how
    detectable the difference is and `rank.effect` says how large; which of
    those matters, and at what threshold, is a decision belonging to whoever
    reads the result. An instrument that returned `improved: bool` would be
    making it on their behalf with none of the context.

    `order` is the schedule as it actually ran, which makes the session
    replayable against `seed` and lets a reader check that the interleaving
    was what it claims to be.
    """

    label_a: str
    label_b: str
    run_a: TimingRun
    run_b: TimingRun
    rank: RankTest
    order: tuple[str, ...]
    seed: int
    rounds: int


def compare(  # noqa: PLR0913
    # Seven arguments, against a limit of five. The three keyword ones are not
    # decoration: `label_*` names the conditions in `order`, which is what makes
    # a session readable in the experiment log; `seed` is what makes it
    # reproducible; `fresh_process_per_sample` is a fact only the caller knows
    # and mislabels every sample if it is guessed. Collapsing them into an
    # options object would hide them, and S-1.6 names this signature.
    variant_a: Callable[[], object],
    variant_b: Callable[[], object],
    n: int,
    *,
    label_a: str = "a",
    label_b: str = "b",
    seed: int | None = None,
    fresh_process_per_sample: bool = False,
) -> InterleavedComparison:
    """Run both variants `n` times each, interleaved, and compare them.

    Both arguments are callables and are invoked here. Nothing in this signature
    accepts a measurement taken earlier, because comparing against a stored
    baseline is the specific false-positive source this operation exists to
    remove.

    `n` is per variant, so the session performs `2 * n` calls. It has a floor of
    `MINIMUM_GROUP_SIZE`, checked before anything runs: the rank test refuses
    below that, and discovering it after taking every sample would waste the
    whole session.

    `seed` is recorded on the result whether it was supplied or drawn. Passing
    the recorded seed back reproduces the exact order.

    Sample durations come from `time()`, so everything it refuses to do on the
    caller's behalf still holds here — no warmup discard, no batching, no
    garbage-collection control, every sample returned.

    `Sample.index` is the position in the **session**, not in the variant's own
    run, so `run_a` carries indices like 0, 3, 4, 7. That is the number a reader
    needs: plotting duration against it is how drift becomes visible, and drift
    is what the interleaving is cancelling.

    Raises:
        TypeError: a variant is not callable — most likely samples measured
            earlier, which is the one thing this must not accept.
        ValueError: `n` is below `MINIMUM_GROUP_SIZE`.
        ComparisonError: a variant raised. The round is on the error.
    """
    # Widened to `object` so the check is not typed out of existence. The
    # annotations say both are callable; this guard is for the callers that are
    # not type-checked, which is the interesting half — an agent assembling a
    # comparison at runtime from an artifact it read.
    candidates: tuple[tuple[str, object], ...] = ((label_a, variant_a), (label_b, variant_b))
    for label, variant in candidates:
        if not callable(variant):
            # Reaching this with a list of durations is the failure mode the
            # whole operation is built around, so it is worth saying plainly
            # rather than letting it surface as "'list' object is not callable"
            # from inside the timing loop.
            message = (
                f"variant {label!r} is a {type(variant).__name__}, not a callable; "
                "compare() runs both variants itself and cannot accept measurements "
                "taken earlier — comparing against a stored baseline is the "
                "false-positive source this exists to remove"
            )
            raise TypeError(message)

    if n < MINIMUM_GROUP_SIZE:
        message = (
            f"n must be at least {MINIMUM_GROUP_SIZE} per variant, got {n}; "
            "the rank test refuses smaller groups and the samples would be wasted"
        )
        raise ValueError(message)

    if seed is None:
        seed = random.getrandbits(_SEED_BITS)
    rng = random.Random(seed)

    variants = {label_a: variant_a, label_b: variant_b}
    samples: dict[str, list[Sample]] = {label_a: [], label_b: []}
    order: list[str] = []

    position = 0
    for round_index in range(n):
        # A fresh order per round rather than one shuffle over the session. See
        # the module docstring: a single shuffle can front-load one condition,
        # which is the block design this replaces.
        pair = [label_a, label_b]
        rng.shuffle(pair)

        for label in pair:
            state = (
                ProcessState.FRESH
                if fresh_process_per_sample or position == 0
                else ProcessState.REUSED
            )
            seconds = _time_one(variants[label], label, round_index, n)
            samples[label].append(Sample(index=position, seconds=seconds, process_state=state))
            order.append(label)
            position += 1

    run_a = TimingRun(samples=tuple(samples[label_a]))
    run_b = TimingRun(samples=tuple(samples[label_b]))

    return InterleavedComparison(
        label_a=label_a,
        label_b=label_b,
        run_a=run_a,
        run_b=run_b,
        rank=rank_test(run_a.durations, run_b.durations),
        order=tuple(order),
        seed=seed,
        rounds=n,
    )


def _time_one(fn: Callable[[], object], label: str, round_index: int, rounds: int) -> float:
    """One sample, measured by `time()` so the clock lives in exactly one place.

    The labelling `time()` would apply is discarded rather than duplicated: it
    scopes `FRESH` to its own run, and every single-sample run believes it is
    the first. In a session the run is all `2 * n` samples, so the state is
    decided by the caller of this function.

    The `TimingError` from a one-sample run is unwrapped rather than chained.
    It would sit between the `ComparisonError` and the real exception saying
    "sample 0 of 1", which is an artifact of measuring one at a time and tells
    a reader nothing about a session of `2 * n`.
    """
    try:
        return time(fn, 1).samples[0].seconds
    except TimingError as error:
        raise ComparisonError(label, round_index, rounds) from error.__cause__
