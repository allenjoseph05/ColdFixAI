"""Insertion sort, which is slow for a shape rather than for a size.

S-3.17. `01-primitives.md` §14 cites SlowFuzz's 41.59× slowdown on insertion sort
as the canonical result, so insertion sort is the subject here — and its cost is
counted rather than timed, because a search guided by a timer on a 200µs workload
is hill-climbing the noise floor S-0.4 measured at ~20ms.

The test that matters most is the **control**: the same campaign with targeting
switched off. Any fuzzer finds a bad input eventually, so a guided run that finds
one proves nothing on its own. The guided run has to beat the unguided one, or
the fitness function is decoration.

The second one is the text case, which asserts that guidance does **not** work —
Hypothesis's optimiser hill-climbs numeric draws only, so a text strategy is
silently a random sample. A module that claimed to search text would be claiming
something this test shows is false.
"""

from __future__ import annotations

import time

import pytest
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument

from coldfix.primitives.input_search import (
    BudgetError,
    Campaign,
    Candidate,
    Disclosure,
    InputSearchError,
    confirm,
    search,
)
from coldfix.primitives.registry import (
    REGISTRY,
    Applicability,
    Capability,
    PrimitiveUnavailableError,
    ProjectFact,
    ProjectProfile,
)

COMPARISONS = {"count": 0}

# Written out rather than imported from `MINIMUM_COMPARABLE`. A fixture built
# from the constant it is testing moves with it: lowering the minimum to 1 built
# a campaign with zero peers, and the test asserting that too few peers leave the
# question open passed against a module that had stopped requiring any. These
# pin the documented value of 5 from both sides.
ENOUGH_PEERS = 5
FEWER_THAN_ENOUGH = 4

LISTS = st.lists(st.integers(0, 255), min_size=0, max_size=30)
TEXT = st.text(alphabet="ab", min_size=0, max_size=30)


def insertion_sort(values: list[int]) -> list[int]:
    """The subject. Quadratic on a reversed list, linear on a sorted one.

    Every comparison is counted, so the fitness function reads a deterministic
    number. That is not a convenience for the test: §12 makes the same point for
    the whole project — a search against a counter beats a search against a timer
    whenever the subject has a counter to offer.
    """
    COMPARISONS["count"] = 0
    out = list(values)
    for i in range(1, len(out)):
        j = i
        while j > 0:
            COMPARISONS["count"] += 1
            if out[j - 1] <= out[j]:
                break
            out[j - 1], out[j] = out[j], out[j - 1]
            j -= 1
    return out


def comparisons() -> dict[str, float]:
    return {"comparisons": float(COMPARISONS["count"])}


def sort_campaign(*, guided: bool, examples: int = 200) -> Campaign[list[int]]:
    return search(
        insertion_sort,
        LISTS,
        label="insertion_sort",
        metric="comparisons",
        extra_counters=comparisons,
        examples=examples,
        guided=guided,
    )


def candidate(cost: float, size: int, payload: str = "x") -> Candidate[str]:
    """A candidate stated directly, for the tests about the arithmetic."""
    return Candidate(payload=payload, cost=cost, metrics={"seconds": cost}, size=size)


def campaign_of(*candidates: Candidate[str]) -> Campaign[str]:
    return Campaign(
        label="a parser",
        metric="seconds",
        engine="hypothesis (stated)",
        candidates=candidates,
        seconds_spent=1.0,
        budget_seconds=60.0,
        seed=None,
        guided=True,
        stopped_at_deadline=False,
    )


# ------------------------------- AC 1 and 2: an existing engine, guided by cost


def test_the_guided_search_beats_the_same_search_unguided() -> None:
    """AC 2, and the only test here that can prove it.

    A fuzzer that ran long enough would find a reversed list by accident, so
    finding one proves nothing. What the fitness function has to buy is a *worse*
    worst case than the same budget spent sampling at random.
    """
    guided = sort_campaign(guided=True)
    unguided = sort_campaign(guided=False)

    assert guided.worst.cost > unguided.worst.cost


def test_the_search_maximises_the_metric_it_was_given() -> None:
    """Resource consumption, not coverage. The champion is a shape — a list sorted
    the wrong way round — rather than the longest list tried."""
    campaign = sort_campaign(guided=True)

    worst = campaign.worst
    insertion_sort(sorted(worst.payload))
    same_size_sorted = float(COMPARISONS["count"])

    assert worst.cost == max(c.cost for c in campaign.candidates)
    assert worst.cost > 2 * same_size_sorted


def test_the_inputs_come_from_the_engine_and_cannot_be_handed_over() -> None:
    """AC 1, structurally. There is no parameter that takes inputs, and a list
    passed where the strategy goes is refused by the engine — because a list of
    inputs would make *this* module the generator, which §14's implementation
    note forbids."""
    with pytest.raises(InvalidArgument):
        search(
            insertion_sort,
            [[3, 2, 1], [1, 2, 3]],  # type: ignore[arg-type]
            label="a list of inputs",
            examples=5,
        )


def test_the_campaign_records_which_engine_searched() -> None:
    """AC 1. Whoever reads a null result needs to know what did the searching,
    because "nothing found" is a statement about the engine as much as the
    subject."""
    campaign = sort_campaign(guided=True, examples=50)

    assert campaign.engine.startswith("hypothesis ")


def test_a_text_strategy_is_not_actually_searched() -> None:
    """The limit, asserted rather than described.

    `hypothesis/internal/conjecture/optimiser.py` hill-climbs `integer`, `float`,
    `bytes` and `boolean` nodes and skips everything else, so a campaign over
    `st.text()` is an unguided random sample wearing a guided campaign's clothes.
    If a Hypothesis upgrade ever fixes this, this test fails and the module
    docstring needs rewriting — which is the point of pinning it.
    """

    def count_a(payload: str) -> int:
        hot = payload.count("a")
        COMPARISONS["count"] = hot * hot
        return hot

    def run(*, guided: bool) -> float:
        return search(
            count_a,
            TEXT,
            label="count_a",
            metric="comparisons",
            extra_counters=comparisons,
            examples=200,
            guided=guided,
        ).worst.cost

    assert run(guided=True) == run(guided=False)


# ----------------------------------------------- AC 5: the budget is a refusal


def test_a_budget_longer_than_the_cap_is_refused() -> None:
    """ADR 044's rule, and S-3.15 is where it was learned: a silent clamp turns a
    rejected argument into a commitment of the cap's whole duration."""
    with pytest.raises(BudgetError, match="Refused rather than shortened"):
        sort_campaign_with(seconds=5 * 60 * 60)


def test_a_zero_budget_is_refused() -> None:
    with pytest.raises(BudgetError, match="positive budget"):
        sort_campaign_with(seconds=0)


def test_zero_examples_is_refused() -> None:
    with pytest.raises(BudgetError, match="positive budget"):
        sort_campaign_with(examples=0)


def sort_campaign_with(**overrides: float) -> Campaign[list[int]]:
    arguments: dict[str, object] = {
        "label": "insertion_sort",
        "metric": "comparisons",
        "extra_counters": comparisons,
        "examples": 20,
    }
    arguments.update(overrides)
    return search(insertion_sort, LISTS, **arguments)  # type: ignore[arg-type]


def test_the_campaign_stops_when_the_budget_runs_out() -> None:
    """The cap is checked between examples, and the campaign says when it bit."""

    def slow(payload: list[int]) -> int:
        time.sleep(0.03)
        return len(payload)

    campaign = search(
        slow,
        LISTS,
        label="a slow subject",
        examples=1000,
        seconds=0.3,
    )

    assert campaign.stopped_at_deadline
    assert len(campaign.candidates) < 1000
    assert "stopped on its" in campaign.report()


def test_a_slow_input_is_recorded_rather_than_failing_the_campaign() -> None:
    """Hypothesis's per-example deadline defaults to 200ms and would fail the
    campaign on precisely the input it was run to find. It is switched off, and
    this is the test that says so."""

    def sometimes_slow(payload: list[int]) -> int:
        if len(payload) > 4:
            time.sleep(0.25)
        return len(payload)

    campaign = search(sometimes_slow, LISTS, label="a subject with a cliff", examples=15)

    assert any(c.metrics["seconds"] > 0.2 for c in campaign.candidates)


# --------------------------------- AC 4: findings that are vulnerability reports


def test_an_input_costing_an_order_of_magnitude_more_is_restricted() -> None:
    """AC 4. The asymmetry *is* the finding: the sender spends the same number of
    bytes and the subject spends ten times the work."""
    result = campaign_of(
        *[candidate(cost=1.0, size=100) for _ in range(ENOUGH_PEERS)],
        candidate(cost=40.0, size=100, payload="(a+)+$"),
    )

    assert result.amplification == pytest.approx(40.0)
    assert result.disclosure is Disclosure.RESTRICTED


def test_a_restricted_report_does_not_contain_the_payload() -> None:
    """The property, attempted rather than described. At this ratio the payload
    is a working exploit, and a report is the thing that gets pasted places."""
    result = campaign_of(
        *[candidate(cost=1.0, size=100) for _ in range(ENOUGH_PEERS)],
        candidate(cost=40.0, size=100, payload="ATTACK-PAYLOAD"),
    )

    report = result.report()

    assert "ATTACK-PAYLOAD" not in report
    assert "vulnerability report" in report
    assert result.witness() == "ATTACK-PAYLOAD"


def test_an_ordinary_finding_prints_its_input() -> None:
    """The other half. Withholding every payload would make the ordinary case
    useless, and then somebody would print them all by hand."""
    result = campaign_of(
        *[candidate(cost=1.0, size=100) for _ in range(ENOUGH_PEERS)],
        candidate(cost=2.0, size=100, payload="ORDINARY-INPUT"),
    )

    assert result.disclosure is Disclosure.ORDINARY
    assert "ORDINARY-INPUT" in result.report()


def test_a_bigger_input_costing_more_is_not_a_complexity_attack() -> None:
    """§14's whole distinction. Scaling varies how much; this varies which. A
    worst case that is simply the largest input tried is `scale_volume`'s finding
    and it answers it better."""
    result = campaign_of(
        *[candidate(cost=1.0, size=10) for _ in range(ENOUGH_PEERS)],
        candidate(cost=40.0, size=400),
    )

    assert result.comparable == ()
    assert result.amplification is None
    assert result.disclosure is Disclosure.UNDETERMINED


def test_too_few_equally_large_inputs_leaves_the_question_open() -> None:
    """One short of the minimum, deliberately.

    The champion does not count towards its own control. Counting it would make
    this campaign look measurable, and would put the largest value in its own
    denominator — which pulls the median up and understates every asymmetry, in
    the direction of not reporting a vulnerability.
    """
    result = campaign_of(
        *[candidate(cost=1.0, size=100) for _ in range(FEWER_THAN_ENOUGH)],
        candidate(cost=40.0, size=100),
    )

    assert len(result.comparable) == FEWER_THAN_ENOUGH
    assert result.disclosure is Disclosure.UNDETERMINED
    assert result.amplification is None


def test_an_undetermined_finding_withholds_its_payload_too() -> None:
    """Failing closed. The case where nobody has established that an input is
    safe to circulate is not the case to print it in."""
    result = campaign_of(
        candidate(cost=1.0, size=10),
        candidate(cost=40.0, size=400, payload="UNKNOWN-PAYLOAD"),
    )

    assert "UNKNOWN-PAYLOAD" not in result.report()
    assert result.witness() == "UNKNOWN-PAYLOAD"


def test_an_input_with_no_size_cannot_be_compared_by_size() -> None:
    """An integer has no length, and reporting 0 or 1 would make every such
    campaign look like it held size constant when it has no notion of size."""
    campaign = search(
        lambda n: n * n,
        st.integers(0, 1000),
        label="squaring",
        examples=30,
    )

    assert all(c.size is None for c in campaign.candidates)
    assert campaign.disclosure is Disclosure.UNDETERMINED


# ------------------------------------- a candidate is not yet a finding


def test_the_report_says_the_champion_is_not_a_finding() -> None:
    """One sample per input, below the noise floor, selected for being the
    extreme of a noisy population — which is how a false positive is made."""
    result = campaign_of(
        *[candidate(cost=1.0, size=100) for _ in range(ENOUGH_PEERS)],
        candidate(cost=2.0, size=100),
    )

    assert "candidate, not a finding" in result.report()
    assert "confirm" in result.report()


def test_confirming_the_champion_measures_both_inputs_properly() -> None:
    """S-1.6's interleaved comparison is the thing allowed to say one is slower
    than the other. The search only proposes."""

    def sort_slowly(values: list[int]) -> list[int]:
        return insertion_sort(values)

    campaign = sort_campaign(guided=True)
    comparison = confirm(sort_slowly, campaign, n=8, seed=7)

    assert comparison.label_a == "worst input found"
    assert len(comparison.run_a.samples) == 8
    assert len(comparison.run_b.samples) == 8


def test_confirming_without_an_equally_large_input_is_refused() -> None:
    """The same condition that leaves disclosure undetermined: with nothing the
    same size to compare against, any difference measured is about size."""
    campaign = campaign_of(candidate(cost=1.0, size=10), candidate(cost=40.0, size=400))

    with pytest.raises(InputSearchError, match="no equally large input"):
        confirm(lambda payload: payload, campaign, n=8)


def test_a_campaign_that_measured_nothing_is_an_error() -> None:
    with pytest.raises(InputSearchError, match="measured no inputs at all"):
        campaign_of()


def test_a_metric_the_run_did_not_measure_is_refused() -> None:
    """ADR 013's rule, applied to the fitness function: a typo cannot become a
    flat search that reports nothing found."""
    with pytest.raises(InputSearchError, match="which this run did not measure"):
        search(insertion_sort, LISTS, label="typo", metric="comparsions", examples=5)


# ------------------------------------------------ AC 3: the applicability gate


def test_the_primitive_is_withheld_where_nobody_chooses_the_input() -> None:
    """AC 3. A worst-case input is only reachable by whoever chooses the input."""
    primitive = REGISTRY.get("inputs.search")
    internal = ProjectProfile(
        capabilities={Capability.INPUT_MUTATION},
        facts={ProjectFact.PARSES_UNTRUSTED_INPUT: False},
    )

    verdict = primitive.verdict(internal)

    assert verdict.applicability is Applicability.NOT_APPLICABLE
    assert "no attacker to search on behalf of" in verdict.reason


def test_the_primitive_is_withheld_where_the_fact_is_unknown() -> None:
    """Three answers, not two. Nobody has established this one, which calls for a
    different action than a subject known not to parse untrusted input."""
    primitive = REGISTRY.get("inputs.search")

    verdict = primitive.verdict(ProjectProfile(capabilities={Capability.INPUT_MUTATION}))

    assert verdict.applicability is Applicability.UNDETERMINED


def test_the_primitive_is_offered_where_input_is_user_controlled() -> None:
    primitive = REGISTRY.get("inputs.search")
    exposed = ProjectProfile(
        capabilities={Capability.INPUT_MUTATION},
        facts={ProjectFact.PARSES_UNTRUSTED_INPUT: True},
    )

    assert primitive.verdict(exposed).applicability is Applicability.APPLICABLE


def test_the_selection_refuses_it_by_name_with_the_reason() -> None:
    selection = REGISTRY.select(
        ProjectProfile(
            capabilities=frozenset(Capability),
            facts={ProjectFact.PARSES_UNTRUSTED_INPUT: False},
        )
    )

    with pytest.raises(PrimitiveUnavailableError, match="no attacker to search on behalf of"):
        selection.get("inputs.search")
