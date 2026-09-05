"""S-20.2 and S-20.3.

The tests that matter here are the ones about what a report is *not allowed* to
imply: that it looked everywhere, that a suspicion is worth a number, or that a
run which exercised nothing found nothing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement, Mode, Spread
from coldfix.collect.tiers import Capabilities, Probe, Tier
from coldfix.evidence.ledger import Basis, Citation, Claim, Finding, Ledger, Location, Proof
from coldfix.evidence.report import (
    Coverage,
    FindingsWithoutCoverageError,
    Report,
    unmeasurable,
)


def bare(identifier: str, median: float) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "app.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=1,
        wall=Spread(median=median, low=median, high=median),
        cpu_s=median,
        mode=Mode.COMPUTING,
        peak_rss_bytes=1024,
        read_bytes=0,
        write_bytes=0,
    )


def ablation(identifier: str, share: float = 0.784) -> AblationMeasurement:
    return AblationMeasurement(
        measurement_id=identifier,
        symbol="Author.books",
        file="app/models.py",
        line=112,
        before=bare(f"{identifier}-b", 2.41),
        after=bare(f"{identifier}-a", 0.52),
        share_removed=share,
        output_changed=True,
    )


TIER_ONE = Capabilities(
    image="python:3.12-slim",
    tier=Tier.INSTRUMENTED,
    probes=(Probe(name="runs_a_process", achieved=True, detail="ok"),),
)
TIER_ZERO = Capabilities(
    image="busybox:stable",
    tier=Tier.OS_ONLY,
    probes=(Probe(name="accepts_instrumentation", achieved=False, detail="no pip"),),
)
DRIVER = "import app\napp.main(200)\n"


def ledger_with_everything() -> Ledger:
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    ledger.record(bare("m-2", 1.10))
    return ledger


def proven_finding(ledger: Ledger, payoff: float = 0.784, tag: str = "m-abl") -> Finding:
    """Records an ablation whose numbers are the ones the claim will cite.

    Written this way after the first version cited a share the recorded ablation
    did not have and was refused -- which is the ledger doing its job, and a
    reminder that a proof is a reference to a measurement rather than a place to
    write a number.
    """
    ledger.record(ablation(tag, share=payoff))
    return ledger.attest(
        Claim(
            kind="repeated_query",
            summary="the serializer reads .books inside the loop",
            location=Location(file="app/models.py", line=112, symbol="Author.books"),
            evidence=(Citation(measurement_id="m-1", field="cpu_s", value=2.41),),
            basis=Basis.ABLATION,
            payoff=payoff,
            proof=Proof(measurement_id=tag, before=2.41, after=0.52, share_removed=payoff),
        )
    )


def suspected_finding(ledger: Ledger, summary: str = "10,500 allocations per item") -> Finding:
    return ledger.attest(
        Claim(
            kind="allocation_churn",
            summary=summary,
            location=Location(file="app/render.py", line=88, symbol="render"),
            evidence=(Citation(measurement_id="m-2", field="cpu_s", value=1.10),),
        )
    )


def report(**overrides: object) -> Report:
    ledger = ledger_with_everything()
    defaults = {
        "subject": "django-helpdesk",
        "capabilities": TIER_ONE,
        "coverage": Coverage(driven=("GET /api/books",), not_driven=("47 other routes",)),
        "driver": DRIVER,
        "findings": (proven_finding(ledger), suspected_finding(ledger)),
        "not_measured": (),
    }
    return Report(**{**defaults, **overrides})  # type: ignore[arg-type]


# ------------------------------------------ what a report may not leave unsaid


def test_a_report_without_not_measured_cannot_be_built() -> None:
    """No default. A default would let a report that never checked the allocator
    look identical to one that checked and found nothing, and only the second may
    say "not the allocator"."""
    ledger = ledger_with_everything()
    with pytest.raises(ValidationError, match="not_measured"):
        Report(
            subject="s",
            capabilities=TIER_ONE,
            coverage=Coverage(driven=("x",)),
            driver=DRIVER,
            findings=(proven_finding(ledger),),
            # The type error here is the point: `not_measured` has no default, so
            # omitting it is caught by mypy *and* at runtime. Silencing the first
            # is what lets the test prove the second.
        )  # type: ignore[call-arg]


def test_an_empty_not_measured_is_a_claim_somebody_had_to_make() -> None:
    """Writing `()` says *nothing was out of reach*. It is allowed, and it has to
    be typed rather than fallen into."""
    assert report(not_measured=()).not_measured == ()


def test_what_the_tier_could_not_reach_appears_even_when_nothing_else_does() -> None:
    """The run may have no gaps of its own and still be blind to four things
    because of the image it ran in."""
    rendered = report(capabilities=TIER_ZERO, not_measured=()).render()
    assert "NOT MEASURED" in rendered
    for missing in ("stacks_with_line_numbers", "allocations", "spans"):
        assert missing in rendered


def test_coverage_is_printed_before_the_findings() -> None:
    """The limits go first. A reader who stops after the headline should already
    know what the run did not look at."""
    rendered = report().render()
    assert rendered.index("did not drive") < rendered.index("PROVEN")


# ------------------------------------------- a run that drove nothing found nothing


def test_findings_from_a_run_that_drove_nothing_are_refused() -> None:
    """A finding describes something observed happening. A run that exercised
    nothing observed nothing."""
    ledger = ledger_with_everything()
    with pytest.raises(FindingsWithoutCoverageError, match="drove no code path"):
        Report(
            subject="s",
            capabilities=TIER_ONE,
            coverage=Coverage(driven=()),
            driver="",
            findings=(proven_finding(ledger),),
            not_measured=(),
        )


def test_the_unmeasurable_report_has_its_own_shape() -> None:
    """An ordinary report with an empty findings list and a report that could not
    run the program look the same on the page and mean opposite things."""
    blank = unmeasurable("their-app", TIER_ZERO, "dependency install failed")
    assert "could not be measured" in blank.headline()
    assert blank.findings == ()
    assert blank.not_measured[0].why == "dependency install failed"


def test_nothing_found_is_printed_as_a_result_not_as_silence() -> None:
    """A run that proves nothing wasteful is an answer, and the report says so
    rather than showing an empty space."""
    rendered = report(findings=()).render()
    assert "NOTHING FOUND" in rendered
    assert "a run that proves nothing wasteful is a result" in rendered


# --------------------------------------------- proven and suspected never mix


def test_proven_findings_are_ordered_by_what_removing_the_work_is_worth() -> None:
    ledger = ledger_with_everything()
    small = proven_finding(ledger, payoff=0.312, tag="m-small")
    big = proven_finding(ledger, payoff=0.784, tag="m-big")
    ordered = report(findings=(small, big)).proven
    assert [f.claim.payoff for f in ordered] == [0.784, 0.312]


def test_a_suspicion_never_outranks_a_proof_however_it_is_worded() -> None:
    """A suspicion carries no number. Ranking the two lists together would mean
    inventing one, or reading a missing payoff as zero -- a fabrication, or a way
    to bury real suspicions under trivial proofs."""
    ledger = ledger_with_everything()
    loud = suspected_finding(ledger, summary="ENORMOUS: 40x more allocations than items")
    tiny = proven_finding(ledger, payoff=0.01, tag="m-tiny")
    built = report(findings=(loud, tiny))

    assert built.proven == (tiny,)
    assert built.suspected == (loud,)
    rendered = built.render()
    assert rendered.index("PROVEN") < rendered.index("SUSPECTED")
    assert rendered.index("ENORMOUS") > rendered.index("PROVEN")


def test_a_suspicion_is_printed_without_a_percentage() -> None:
    rendered = report(findings=(suspected_finding(ledger_with_everything()),)).render()
    assert "carrying no number" in rendered
    assert "%" not in rendered.split("SUSPECTED")[1].split("NOT MEASURED")[0]


# ------------------------------------------------------------- the driver


def test_the_driver_that_produced_every_number_is_in_the_report() -> None:
    """Twenty lines somebody can read to see what was actually run. It is the
    honest answer to "how do you know the measurement is real"."""
    rendered = report().render()
    assert "DRIVER" in rendered
    for line in DRIVER.splitlines():
        assert line in rendered


def test_every_proven_finding_names_the_measurements_behind_it() -> None:
    rendered = report().render()
    assert "attested against m-1, m-abl" in rendered


def test_a_report_survives_a_checkpoint() -> None:
    original = report()
    revived = Report.model_validate_json(original.model_dump_json())
    assert revived.render() == original.render()
