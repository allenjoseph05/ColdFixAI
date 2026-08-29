"""The interpreted half of a chain, and the two ways it must refuse.

S-8.11. `explain` is the first thing in this project that asks a model for the
*conclusion* rather than for one experiment's reading, so the tests that matter
are the ones proving it cannot smuggle a number in and cannot be asked about an
investigation that established nothing.

Model calls are replayed; nothing here reaches an API.
"""

from __future__ import annotations

import re

import pytest

from coldfix.cost.cascade import cascadable
from coldfix.cost.routing import StepType
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.explain import (
    Explanation,
    ExplanationError,
    explain,
    parse,
    render_question,
    shares_from,
)
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict
from coldfix.llm.client import ReplayingClient
from fixtures.thesis import a_session

GOOD = """\
{"mechanism": "the renderer walks every row and re-renders its synopsis",
 "site": {"path": "shop/rendering.py", "first_line": 54, "last_line": 61},
 "context": [{"path": "shop/views.py", "reason": "constructs the renderer per row"}]}"""


def an_experiment(
    log: ExperimentLog, *, measurement: dict[str, float], target: str = "ExpensiveRenderer.render"
) -> Experiment:
    return log.append(
        hypothesis="the renderer owns the cost",
        primitive="ablation.stub",
        rationale="scaling came back flat, so the database is excluded",
        target=target,
        design=f"ablation.stub(target={target!r}) on shop.books.list",
        measurement=measurement,
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the renderer removed almost all of the wall time",
    )


# ============================================ the step is allowed to cascade


def test_the_evidence_chain_step_may_cascade_and_names_the_check() -> None:
    """**Why a cascade is safe here and not on the two steps `CLAUDE.md` names.**

    Hypothesis generation and attack design record *none exists* for a mechanical
    check, so a cheaper tier's answer cannot be told from a good one. This step
    has one — the schema — and a tier that invents a site produces a chain the
    schema refuses.
    """
    assert cascadable()[StepType.EVIDENCE_CHAIN] == "schema requires a measurement"


# ============================================ the reply cannot carry a number


def test_a_reply_becomes_an_explanation() -> None:
    attempt = parse(GOOD)

    assert attempt.valid, attempt.rejection
    assert isinstance(attempt.value, Explanation)
    assert attempt.value.site.path == "shop/rendering.py"
    assert attempt.value.context[0].reason == "constructs the renderer per row"


@pytest.mark.parametrize(
    "smuggled",
    [
        '"confidence": 0.9',
        '"seconds.share_removed": 0.87',
        '"measurement": {"seconds": 8.24}',
    ],
)
def test_a_reply_that_reports_a_figure_is_refused(smuggled: str) -> None:
    """**The first non-negotiable, kept by construction.** `Explanation` has no
    field a number could arrive in, and `extra="forbid"` turns the attempt into a
    rejection rather than a key quietly dropped on the floor.

    Three spellings because a model that wants to report a figure will reach for
    whichever word the prompt used, and a schema that refused only one of them
    would be refusing a vocabulary rather than a behaviour.
    """
    attempt = parse(GOOD.replace('{"mechanism"', "{" + smuggled + ', "mechanism"'))

    assert not attempt.valid
    assert "extra" in attempt.rejection.lower() or "not permitted" in attempt.rejection.lower()


@pytest.mark.parametrize("mechanism", ['""', "null"])
def test_a_reply_with_no_mechanism_is_refused(mechanism: str) -> None:
    """A site and a file list with no causal story is a location, not a finding."""
    attempt = parse(
        '{"mechanism": '
        + mechanism
        + ', "site": {"path": "a.py", "first_line": 1, "last_line": 2}, "context": []}'
    )

    assert not attempt.valid
    assert "mechanism" in attempt.rejection


def test_an_implicated_file_with_no_reason_is_refused() -> None:
    """`02-architecture.md` §3 makes this list decide what a repair may edit, so a
    file admitted with no reason is a file the Surgeon may edit because somebody
    felt it was relevant."""
    attempt = parse(GOOD.replace('"constructs the renderer per row"', '"   "'))

    assert not attempt.valid
    assert "reason" in attempt.rejection


def test_a_reply_that_is_not_json_says_so_rather_than_raising() -> None:
    """Every failure at this boundary is the correctable kind, and the sentence is
    written to be handed to the next attempt."""
    attempt = parse("I think the renderer is slow.")

    assert not attempt.valid
    assert "no JSON object" in attempt.rejection


# ============================================ the share comes off the record


def test_shares_come_off_the_measurement_the_primitive_recorded() -> None:
    """The scope is what the instrument was pointed at and the share is a number
    the primitive computed — both read off the record rather than supplied."""
    log = ExperimentLog()
    experiment = an_experiment(log, measurement={"seconds": 8.24, "seconds.share_removed": 0.87})

    shares = shares_from([experiment])

    scope, share, basis = shares[experiment.index]
    assert scope == "ExpensiveRenderer.render"
    assert share == 0.87
    assert "ablation.stub" in basis
    assert "seconds.share_removed" in basis


def test_a_confirming_experiment_with_no_measured_share_is_refused_by_name() -> None:
    """**Refused by name rather than given a plausible fraction.**

    A share *can* cross the loop boundary — `Executor` returns floats and a
    fraction is a float — so unlike Epic 9's `kinds`, this is not unreachable.
    What it needs is the primitive to have reported one under the name both ends
    agree on. An experiment without it says so and names itself; inventing a
    number here would put a figure nobody measured under a finding.
    """
    log = ExperimentLog()
    experiment = an_experiment(log, measurement={"seconds": 8.24})

    with pytest.raises(ExplanationError, match=re.escape("recorded no 'seconds.share_removed'")):
        shares_from([experiment])


def test_the_refusal_names_every_experiment_that_is_missing_one() -> None:
    """Naming the first would send somebody round the loop once per experiment."""
    log = ExperimentLog()
    first = an_experiment(log, measurement={"seconds": 8.24})
    second = an_experiment(log, measurement={"seconds": 4.10}, target="Synopsis.render")

    with pytest.raises(ExplanationError, match=r"\[1, 2\]"):
        shares_from([first, second])

    assert (first.index, second.index) == (1, 2)


# ============================================ nothing confirmed is never asked


def test_an_investigation_that_confirmed_nothing_is_never_asked() -> None:
    """**Asked before paid for.** A run with no cause owes a partial chain, and
    putting the question to a model would be the one place a finding could be
    written without a measurement under it — at the price of a frontier call.

    The client holds no recordings, so a call would fail loudly rather than
    quietly succeed — but the assertion is on `served`, because *it raised* and
    *it raised before spending* are different claims.
    """
    client = ReplayingClient([])

    with pytest.raises(ExplanationError, match="confirmed nothing"):
        explain(
            a_session(),
            client,
            symptom=Symptom(metric="seconds", at_scale=1000.0, magnitude=8.24),
            confirming=(),
            exclusions=(),
            measured_prefix_tokens=8000,
            measured_prompt_tokens=900,
        )

    assert client.served == (), "no tier was called"


# ============================================ the question carries the evidence


def test_the_question_shows_the_measurements_it_asks_about() -> None:
    """A reply reasoning about numbers it was not shown is one nobody can check."""
    log = ExperimentLog()
    experiment = an_experiment(log, measurement={"seconds": 8.24, "seconds.share_removed": 0.87})

    question = render_question(
        symptom=Symptom(metric="seconds", at_scale=1000.0, magnitude=8.24),
        confirming=[experiment],
        exclusions=["the database is the bottleneck — queries flat at 2 across a 4x sweep"],
    )

    assert "seconds=8.24" in question
    assert "ablation.stub on ExpensiveRenderer.render" in question
    assert "queries flat at 2" in question, "what was ruled out is half the argument"
    assert "shop/views.py::ListView.list_books" not in question, (
        "the source is the session's cached block now — S-17.16, which stopped it "
        "being sent at the foot of this question as well"
    )
