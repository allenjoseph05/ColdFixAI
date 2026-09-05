"""S-18.5.

The AC asks for a test that builds a program whose queries the instrument cannot
see and asserts the run fails rather than reporting "no queries". That program
exists: `connection.execute` instead of `cursor.execute`. The real end of it --
OpenTelemetry actually attached, actually emitting nothing -- needs a container
and lives in `tools/attachment_corpus.py`; what is here is the decision the
harness makes once it has the numbers.
"""

from __future__ import annotations

import pytest

from coldfix.collect.attachment import (
    AGREEMENT,
    Basis,
    Expectation,
    InstrumentNotAttachedError,
    InstrumentPartiallyAttachedError,
    expect_from_report,
    reported_by_subject,
    verify_attached,
)


def observed(metric: str, at_least: int = 1) -> Expectation:
    return Expectation(
        metric=metric,
        basis=Basis.OBSERVED_IMPORT,
        witness="grounding saw the subject import sqlite3",
        at_least=at_least,
    )


# ------------------------------------- the correction the corpus forced first


def test_no_expectation_means_no_check() -> None:
    """Six of the eight corpus subjects have no database and correctly emit no
    spans. Stated as "a zero is a failure" the guard fired on all six. Absence is
    the right answer when nothing said otherwise."""
    verify_attached({"db_query": 0, "http_request": 0}, [])


def test_only_the_metrics_with_an_expectation_are_checked() -> None:
    """`db_query` reads zero and is never looked at, because nothing claimed it
    should have moved. Only `http_request` was witnessed."""
    verify_attached({"db_query": 0, "http_request": 7}, [observed("http_request", at_least=5)])


# --------------------------------------------------- the failure it must catch


def test_a_zero_with_a_witness_is_a_failed_measurement_not_a_finding() -> None:
    """The exact shape found on 2026-09-05: the instrument loaded, registered,
    and saw nothing, because the program calls the database by a route it does
    not wrap."""
    with pytest.raises(InstrumentNotAttachedError) as caught:
        verify_attached({"db_query": 0}, [observed("db_query")])
    message = str(caught.value)
    assert "not attached" in message
    assert "not a finding that the work does not happen" in message
    assert "Nothing may be concluded" in message


def test_a_missing_metric_counts_as_zero_not_as_absent() -> None:
    """A key the harvest never wrote is the same failure as one written as 0."""
    with pytest.raises(InstrumentNotAttachedError):
        verify_attached({}, [observed("db_query")])


def test_a_count_that_disagrees_with_the_program_itself_is_refused() -> None:
    """A query count that is 60% of the truth still produces a ratio, a ranking
    and a finding, every one of them quietly wrong."""
    with pytest.raises(InstrumentPartiallyAttachedError, match="invisible to the instrument"):
        verify_attached({"db_query": 96}, [observed("db_query", at_least=161)])


def test_a_count_within_the_agreement_floor_passes() -> None:
    verify_attached({"db_query": 150}, [observed("db_query", at_least=161)])
    assert AGREEMENT < 150 / 161


def test_every_expectation_is_checked_not_just_the_first() -> None:
    with pytest.raises(InstrumentNotAttachedError, match="cache_get"):
        verify_attached(
            {"db_query": 161, "cache_get": 0},
            [observed("db_query", at_least=161), observed("cache_get")],
        )


# ---------------------------------------------- the subject as its own witness


def test_counts_the_program_printed_about_itself_are_parsed() -> None:
    stdout = "authors=2000 chars=418322 queries=2001\n"
    assert reported_by_subject(stdout) == {"authors": 2000, "chars": 418322, "queries": 2001}


def test_a_self_reported_count_becomes_an_expectation() -> None:
    """The strongest witness there is: it comes from inside the program, and the
    instrument comes from outside. When they disagree, the instrument is wrong."""
    expectation = expect_from_report(
        "authors=2000 queries=2001\n", metric="db_query", reported_as="queries"
    )
    assert expectation is not None
    assert expectation.at_least == 2001
    assert expectation.basis is Basis.REPORTED_BY_SUBJECT

    with pytest.raises(InstrumentNotAttachedError):
        verify_attached({"db_query": 0}, [expectation])


def test_silence_from_the_program_creates_no_expectation() -> None:
    """Saying nothing is not a claim that the work happens. A program that does
    not report a count must not be treated as having reported a large one."""
    assert expect_from_report("done\n", metric="db_query", reported_as="queries") is None


def test_a_reported_zero_creates_no_expectation() -> None:
    """`queries=0` is the program agreeing there were none, which is exactly the
    case where a zero measurement is correct."""
    assert expect_from_report("queries=0\n", metric="db_query", reported_as="queries") is None


def test_the_corpus_drivers_all_carry_a_witness() -> None:
    """Every driver prints `queries=N` so this guard always has something to
    check against, including the ones where the honest N is zero."""
    for stdout, expected in (
        ("authors=2000 chars=1 queries=2001", 2001),
        ("rendered=12 chars=1 queries=0", 0),
        ("total=42 queries=0", 0),
    ):
        assert reported_by_subject(stdout)["queries"] == expected
