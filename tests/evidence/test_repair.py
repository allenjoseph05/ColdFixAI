"""E22 — repair as a measured search.

Nothing here calls a model. `propose` is a callable, so the search is exercised
with lists of candidates and the thing under test is the selection, never the
generation.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence

import pytest
from pydantic import ValidationError

from coldfix.evidence.repair import (
    MAX_CANDIDATES,
    WALL,
    Archive,
    Candidate,
    Falsified,
    ForgedTokenError,
    NoFailingTestError,
    Outcome,
    Scored,
    UnmeasuredCandidateError,
    UntimedCandidateError,
    must_fail,
    score,
    search,
)

FAILED = (1, "AssertionError: expected 1 query, got 161")


class Records:
    """A ledger's read side, holding whatever a test puts in it.

    `Mapping` rather than `dict` in the value type, because `dict` is invariant:
    a literal record is a `dict[str, float]`, which is not a `dict[str, object]`.
    """

    def __init__(self, **records: Mapping[str, object]) -> None:
        self._records = records

    @property
    def known(self) -> tuple[str, ...]:
        return tuple(sorted(self._records))

    def recorded(self, measurement_id: str) -> Mapping[str, object] | None:
        return self._records.get(measurement_id)


def gate(result: tuple[int, str] = FAILED) -> Falsified:
    return must_fail("def test_books_prefetched(): ...", lambda _: result)


def candidate(name: str) -> Candidate:
    return Candidate(identifier=f"c-{name}", approach=name, diff=f"--- {name}")


def scored(
    name: str,
    wall: float,
    *,
    peak: int | None = 1024,
    outputs_match: bool = True,
    tests_pass: bool = True,
) -> Scored:
    """A measured candidate. The helper defaults; `Scored` itself does not.

    The defaults live here because most of these tests are about selection and
    say so by varying one field. The model requires both, so a caller that has
    not run the suite cannot quietly claim it passed (S-30.1).
    """
    return Scored(
        candidate=candidate(name),
        measurement_id=f"m-{name}",
        wall_s=wall,
        peak_rss_bytes=peak,
        outputs_match=outputs_match,
        tests_pass=tests_pass,
    )


BASELINE = scored("baseline", 8.42)


def archive_of(*entries: Scored) -> Archive:
    archive = Archive(baseline=BASELINE)
    for entry in entries:
        archive = archive.record(entry)
    return archive


# ------------------------------------- S-30.1, a score minted from a measurement


def test_a_candidates_time_is_the_one_the_harness_recorded() -> None:
    """The whole story. `wall_s` is not a parameter, so there is nothing to pass
    that could disagree with the measurement the id names."""
    records = Records(**{"m-1": {WALL: 2.41, "peak_rss_bytes": 81_234}})

    minted = score(records, candidate("prefetch"), "m-1", outputs_match=True, tests_pass=True)

    assert minted.wall_s == 2.41
    assert minted.peak_rss_bytes == 81_234
    assert minted.measurement_id == "m-1"


def test_a_score_citing_a_measurement_nobody_took_is_refused() -> None:
    """The repair half of *no finding without a measurement*. Before this the id
    was decoration: a candidate could carry a real one beside an invented time."""
    with pytest.raises(UnmeasuredCandidateError, match="not a measurement this run recorded"):
        score(Records(), candidate("prefetch"), "m-9", outputs_match=True, tests_pass=True)


def test_a_measurement_with_no_wall_time_scores_nothing() -> None:
    """A field absent from a measurement was not measured. Choosing a winner on
    it would be choosing on a number nobody took."""
    records = Records(**{"m-1": {"peak_rss_bytes": 81_234}})

    with pytest.raises(UntimedCandidateError, match="has no"):
        score(records, candidate("prefetch"), "m-1", outputs_match=True, tests_pass=True)


def test_a_measurement_without_a_peak_scores_without_one() -> None:
    """Absent is absent. A guard nobody measured is unverified, not satisfied --
    and `_paid_for_elsewhere` already declines to judge on a missing number."""
    records = Records(**{"m-1": {WALL: 2.41}})

    minted = score(records, candidate("prefetch"), "m-1", outputs_match=True, tests_pass=True)

    assert minted.peak_rss_bytes is None


@pytest.mark.parametrize("observation", ["outputs_match", "tests_pass"])
def test_a_score_cannot_be_built_without_saying_what_was_observed(observation: str) -> None:
    """`Side.stable`'s rule, applied to the object with the same shape: a default
    would claim the suite passed and the output held on behalf of a check nobody
    ran, which is the permissive direction."""
    fields = {
        "candidate": candidate("prefetch"),
        "measurement_id": "m-1",
        "wall_s": 2.41,
        "outputs_match": True,
        "tests_pass": True,
    }
    del fields[observation]

    with pytest.raises(ValidationError, match=observation):
        Scored(**fields)  # type: ignore[arg-type]


# --------------------------------------------- S-22.1, the failing-test gate


def test_a_test_that_passes_first_mints_nothing() -> None:
    """A test that passes before you change anything proves the problem is
    absent, not that a fix works."""
    with pytest.raises(NoFailingTestError, match="it passed"):
        gate((0, "1 passed"))


def test_a_broken_test_does_not_authorise_patching() -> None:
    """The gate inverted. A script with a syntax error also exits non-zero, and
    under *non-zero means it failed* it would open the door."""
    for broken in ("SyntaxError: invalid syntax", "ModuleNotFoundError: no module named x"):
        with pytest.raises(NoFailingTestError, match="did not run"):
            gate((2, broken))


def test_a_test_that_really_failed_mints_a_token() -> None:
    token = gate()
    assert token.exit_code == 1
    assert "161" in token.detail


def test_a_token_cannot_be_written_by_hand() -> None:
    """The whole point of making it a value rather than a flag. Proof that can be
    constructed is an assertion."""
    with pytest.raises(ForgedTokenError, match="proof that can be written"):
        Falsified(test="t", exit_code=1, detail="d", minted="trust me")


def test_the_search_cannot_be_entered_without_a_token() -> None:
    """`apply` takes a `Falsified`, so a candidate cannot be measured -- let alone
    applied -- without a failing test having been proved first."""
    assert "falsified" in inspect.signature(search).parameters


# ------------------------------------------------- S-22.2, the measured search


def test_every_candidate_is_measured_not_only_the_promising_ones() -> None:
    """Selection is from the archive. A candidate is not skipped for looking
    unlikely, because looking unlikely is not a measurement."""
    measured: list[str] = []

    def apply(_: Falsified, c: Candidate) -> Scored:
        measured.append(c.approach)
        return scored(c.approach, 4.0)

    search(
        falsified=gate(),
        baseline=BASELINE,
        propose=lambda _: [candidate("cache"), candidate("hoist"), candidate("batch")],
        apply=apply,
    )
    assert measured == ["cache", "hoist", "batch"]


def test_the_winner_is_the_fastest_that_broke_nothing() -> None:
    archive = archive_of(scored("cache", 6.8), scored("hoist", 5.1), scored("batch", 7.9))
    winner = archive.winner
    assert winner is not None
    assert winner.candidate.approach == "hoist"
    assert winner.share_removed(BASELINE) == pytest.approx(0.394, abs=0.001)


def test_a_candidate_that_reads_well_and_measured_slower_is_a_loser() -> None:
    """Nothing is selected for being plausible."""
    archive = archive_of(scored("elegant_rewrite", 9.9), scored("ugly_hack", 5.0))
    winner = archive.winner
    assert winner is not None
    assert winner.candidate.approach == "ugly_hack"
    assert archive.losers[0].candidate.approach == "elegant_rewrite"


def test_faster_but_paid_for_in_memory_is_a_trade_not_a_win() -> None:
    """A candidate that saves a second and costs 300MB is shown as a trade.
    Shipping it silently would be the guard-counter failure this project refuses
    everywhere else."""
    archive = archive_of(scored("lookup_table", 4.0, peak=4096), scored("hoist", 6.0))
    winner = archive.winner
    assert winner is not None
    assert winner.candidate.approach == "hoist", "the trade must not win by being fastest"
    assert [t.candidate.approach for t in archive.trades] == ["lookup_table"]


def test_a_trade_is_reported_rather_than_discarded() -> None:
    """Somebody may want it. What they must not have is it chosen for them."""
    archive = archive_of(scored("lookup_table", 4.0, peak=4096))
    assert archive.winner is None
    assert len(archive.trades) == 1
    assert "nothing beat the baseline" in archive.summary()


def test_a_candidate_that_changes_the_output_is_rejected_however_fast() -> None:
    archive = archive_of(scored("wrong", 0.1, outputs_match=False), scored("right", 8.0))
    winner = archive.winner
    assert winner is not None
    assert winner.candidate.approach == "right"
    assert archive.losers[0].outcome(baseline=BASELINE) is Outcome.BROKE_OUTPUT


def test_a_candidate_that_breaks_the_suite_is_rejected_however_fast() -> None:
    archive = archive_of(scored("fast_and_broken", 0.1, tests_pass=False))
    assert archive.winner is None
    assert archive.losers[0].outcome(baseline=BASELINE) is Outcome.BROKE_TESTS


def test_nothing_beating_the_baseline_is_an_outcome_not_an_error() -> None:
    archive = archive_of(scored("a", 9.0), scored("b", 8.5))
    assert archive.winner is None
    assert "nothing beat the baseline" in archive.summary()


# ---------------------------------------------------- losers persist


def test_a_losing_approach_is_not_proposed_twice() -> None:
    """A search that forgets its failures repeats them, and every repeat is paid
    for twice: once to generate and once to measure."""
    applied: list[str] = []

    def apply(_: Falsified, c: Candidate) -> Scored:
        applied.append(c.approach)
        return scored(c.approach, 9.9)

    def propose(archive: Archive) -> Sequence[Candidate]:
        # Offers the same thing every time, as a tiring model would.
        return [candidate("cache")]

    archive = search(falsified=gate(), baseline=BASELINE, propose=propose, apply=apply)
    assert applied == ["cache"], "the loser was measured a second time"
    assert len(archive.scored) == 1


def test_a_search_seeded_with_earlier_attempts_does_not_measure_them_again() -> None:
    """S-28.5 found this at the join: when the patch audit sends a patch back,
    `optimize` searches again, and a fresh archive has never heard of the
    candidate that just lost -- so it is proposed, measured a second time, and
    written into an append-only channel that refuses it."""
    applied: list[str] = []

    def apply(_: Falsified, c: Candidate) -> Scored:
        applied.append(c.approach)
        return scored(c.approach, 9.9)

    lost = scored("cache", 9.9)
    archive = search(
        falsified=gate(),
        baseline=BASELINE,
        propose=lambda _: [candidate("cache"), candidate("hoist")],
        apply=apply,
        already=[lost],
    )

    assert applied == ["hoist"], "the one that already lost was not measured again"
    assert [entry.candidate.approach for entry in archive.scored] == ["cache", "hoist"]


def test_the_limit_counts_the_candidates_for_a_finding_not_for_a_round() -> None:
    """Eight attempts is eight attempts however many times a patch is sent back."""
    earlier = [scored(f"try{index}", 9.9) for index in range(MAX_CANDIDATES - 1)]

    archive = search(
        falsified=gate(),
        baseline=BASELINE,
        propose=lambda _: [candidate("late"), candidate("later")],
        apply=lambda _, c: scored(c.approach, 9.9),
        already=earlier,
    )

    assert len(archive.scored) == MAX_CANDIDATES


def test_the_proposer_is_shown_what_already_lost() -> None:
    """So a later round can do something different rather than guess again."""
    seen: list[int] = []

    def propose(archive: Archive) -> Sequence[Candidate]:
        seen.append(len(archive.losers))
        return [candidate(f"try{len(archive.scored)}")] if len(archive.scored) < 3 else []

    search(
        falsified=gate(),
        baseline=BASELINE,
        propose=propose,
        apply=lambda _, c: scored(c.approach, 9.9),
    )
    assert seen == [0, 1, 2, 3]


def test_the_search_stops_at_the_limit() -> None:
    """Beyond it the cost of measuring grows faster than the chance that attempt
    nine is the one."""
    calls = {"n": 0}

    def propose(archive: Archive) -> Sequence[Candidate]:
        calls["n"] += 1
        return [candidate(f"a{calls['n']}-{i}") for i in range(5)]

    archive = search(
        falsified=gate(),
        baseline=BASELINE,
        propose=propose,
        apply=lambda _, c: scored(c.approach, 9.9),
        limit=6,
    )
    assert len(archive.scored) == 6
    assert MAX_CANDIDATES == 8


def test_a_proposer_with_nothing_new_ends_the_search() -> None:
    archive = search(
        falsified=gate(),
        baseline=BASELINE,
        propose=lambda _: [],
        apply=lambda _, c: scored(c.approach, 1.0),
    )
    assert archive.scored == ()
    assert archive.winner is None


# ------------------------------------------- S-22.3, output equivalence


def test_output_equivalence_is_byte_for_byte_and_not_a_judgement() -> None:
    """`outputs_match` comes from a digest comparison upstream. There is no
    threshold here and no similarity -- the only definition that needs nobody's
    opinion."""
    assert scored("x", 1.0, outputs_match=False).outcome(baseline=BASELINE) is Outcome.BROKE_OUTPUT
    assert scored("x", 1.0, outputs_match=True).outcome(baseline=BASELINE) is Outcome.WON


def test_the_archive_survives_a_checkpoint() -> None:
    """It goes in run state, so it serializes like everything else -- and the
    losers have to come back, or the next round re-proposes them."""
    archive = archive_of(scored("a", 9.0), scored("b", 4.0))
    revived = Archive.model_validate_json(archive.model_dump_json())
    assert revived.summary() == archive.summary()
    assert revived.already_tried("a")
