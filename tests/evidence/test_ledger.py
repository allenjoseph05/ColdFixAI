"""S-20.1.

The headline test is `test_a_plausible_number_nobody_measured_fails_validation`.
Everything else exists so that one cannot be satisfied by accident.
"""

from __future__ import annotations

import pytest

from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement, Mode, Spread
from coldfix.collect.profiling import ProfileMeasurement
from coldfix.evidence.ledger import (
    Basis,
    Citation,
    Claim,
    EvidenceError,
    FabricatedValueError,
    Finding,
    Ledger,
    Location,
    MissingFieldError,
    Proof,
    ProofIsNotAnAblationError,
    UncitedClaimError,
    UnknownMeasurementError,
    UnprovenPayoffError,
)


def bare(identifier: str, median: float, queries: int = 161) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "app.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=queries,
        wall=Spread(median=median, low=median * 0.99, high=median * 1.01),
        cpu_s=median,
        mode=Mode.COMPUTING,
        peak_rss_bytes=1024,
        read_bytes=0,
        write_bytes=0,
    )


def ablation(identifier: str = "m-abl") -> AblationMeasurement:
    return AblationMeasurement(
        measurement_id=identifier,
        symbol="Author.books",
        file="app/models.py",
        line=112,
        before=bare("m-before", 2.41),
        after=bare("m-after", 0.52),
        share_removed=0.784,
        output_changed=True,
    )


HERE = Location(file="app/models.py", line=112, symbol="Author.books")


def cited(measurement_id: str = "m-1", field: str = "cpu_s", value: float = 2.41) -> Claim:
    return Claim(
        kind="repeated_query",
        summary="the serializer reads .books inside the loop",
        location=HERE,
        evidence=(Citation(measurement_id=measurement_id, field=field, value=value),),
    )


# ---------------------------------------------------- the claim this all exists for


def test_a_plausible_number_nobody_measured_fails_validation() -> None:
    """161 was measured. The claim says 160 -- one off, entirely reasonable, and
    a number nobody took. There is no tolerance because a tolerance would be a
    budget for being slightly wrong about the only thing this system promises to
    be right about."""
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41, queries=161))

    with pytest.raises(FabricatedValueError, match="checked exactly"):
        ledger.attest(cited("m-1", "output_bytes", 160))

    attested = ledger.attest(cited("m-1", "output_bytes", 161))
    assert isinstance(attested, Finding)


def test_a_value_wrong_in_the_last_digit_fails_like_any_other() -> None:
    """Written because sabotaging the exact comparison into `abs(a - b) < 0.01`
    left every other test passing. A difference of one is caught by any tolerance;
    only a difference smaller than the tolerance proves there is not one.

    2.41 was measured. 2.4100001 is not 2.41, and the whole claim of this system
    is that the number in the report is the number the harness took."""
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    for near in (2.4100001, 2.4099999, 2.410000000001):
        with pytest.raises(FabricatedValueError):
            ledger.attest(cited("m-1", "cpu_s", near))
    ledger.attest(cited("m-1", "cpu_s", 2.41))


def test_a_proof_share_wrong_in_the_last_digit_fails_too() -> None:
    """The share is the payoff, so it is the number most worth nudging."""
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    ledger.record(ablation("m-abl"))
    nudged = Claim(
        kind="repeated_query",
        summary="s",
        location=HERE,
        evidence=(Citation(measurement_id="m-1", field="cpu_s", value=2.41),),
        basis=Basis.ABLATION,
        payoff=0.7840001,
        proof=Proof(measurement_id="m-abl", before=2.41, after=0.52, share_removed=0.7840001),
    )
    with pytest.raises(FabricatedValueError, match="share_removed"):
        ledger.attest(nudged)


def test_a_citation_of_a_measurement_that_was_never_taken_is_refused() -> None:
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    with pytest.raises(UnknownMeasurementError, match="m-99"):
        ledger.attest(cited("m-99"))


def test_a_field_absent_from_the_measurement_is_refused() -> None:
    """A field that is not on a measurement was not measured, and the error names
    what actually was, so the next attempt is informed rather than another guess."""
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    with pytest.raises(MissingFieldError) as caught:
        ledger.attest(cited("m-1", "db_queries", 161))
    assert "cpu_s" in str(caught.value)


def test_the_empty_ledger_says_so_rather_than_saying_nothing() -> None:
    with pytest.raises(UnknownMeasurementError, match="none"):
        Ledger().attest(cited())


# ------------------------------------------------- a claim cannot skip the ledger


def test_a_report_cannot_accept_a_claim_because_only_attest_makes_a_finding() -> None:
    """The check is a type, not a habit. There is no path from a claim to a report
    that does not pass through the ledger, so forgetting to validate is not a
    mistake this code can make."""
    assert Finding.model_fields["claim"].annotation is Claim
    assert not isinstance(cited(), Finding)


def test_an_attested_finding_names_every_measurement_behind_it() -> None:
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    ledger.record(ablation("m-abl"))
    claim = Claim(
        kind="repeated_query",
        summary="s",
        location=HERE,
        evidence=(Citation(measurement_id="m-1", field="cpu_s", value=2.41),),
        basis=Basis.ABLATION,
        payoff=0.784,
        proof=Proof(measurement_id="m-abl", before=2.41, after=0.52, share_removed=0.784),
    )
    finding = ledger.attest(claim)
    assert finding.attested_against == ("m-1", "m-abl")
    assert finding.proven


# ----------------------------------------------------- a payoff needs a proof


def test_a_number_cannot_be_attached_to_a_suspicion() -> None:
    """A hot site with a percentage beside it reads as a finding. Until an
    ablation has removed the work and watched the cost go with it, it is a place
    the program happened to be when somebody looked."""
    with pytest.raises(UnprovenPayoffError) as caught:
        Claim(
            kind="hot_site",
            summary="41% of samples landed here",
            location=HERE,
            evidence=(Citation(measurement_id="m-prof", field="counts.samples", value=8140),),
            payoff=0.41,
        )
    assert "no proof behind it" in str(caught.value)


def test_claiming_the_ablation_basis_without_an_ablation_is_refused() -> None:
    with pytest.raises(UnprovenPayoffError):
        Claim(
            kind="repeated_query",
            summary="s",
            location=HERE,
            evidence=(Citation(measurement_id="m-1", field="cpu_s", value=2.41),),
            basis=Basis.ABLATION,
        )


def test_a_suspicion_is_reportable_as_long_as_it_carries_no_number() -> None:
    """Honestly labelled, a suspicion is useful. Wearing a percentage it is not."""
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    finding = ledger.attest(cited("m-1", "cpu_s", 2.41))
    assert not finding.proven
    assert finding.claim.basis is Basis.SUSPECTED
    assert finding.claim.payoff is None


def test_a_claim_citing_nothing_at_all_is_refused() -> None:
    """An opinion, not a finding."""
    with pytest.raises(UncitedClaimError, match="an opinion"):
        Claim(kind="k", summary="s", location=HERE, evidence=())


# --------------------------------------------------------- the proof itself


def test_the_proof_must_cite_an_ablation_not_some_other_measurement() -> None:
    """A profile is a fine thing to cite and it removed nothing, so it cannot be
    what establishes that removing something helps."""
    ledger = Ledger()
    ledger.record(
        ProfileMeasurement(
            measurement_id="m-prof",
            command=("py-spy",),
            repeats=1,
            output_digest="0" * 64,
            output_bytes=0,
            instrument="py-spy",
            counts={"samples": 8140},
        )
    )
    claim = Claim(
        kind="hot_site",
        summary="s",
        location=HERE,
        evidence=(Citation(measurement_id="m-prof", field="counts.samples", value=8140),),
        basis=Basis.ABLATION,
        payoff=0.41,
        proof=Proof(measurement_id="m-prof", before=1.0, after=0.5, share_removed=0.5),
    )
    with pytest.raises(ProofIsNotAnAblationError, match="ProfileMeasurement"):
        ledger.attest(claim)


def test_the_numbers_in_the_proof_are_checked_like_any_other() -> None:
    """The share is the payoff, so it is the number most worth inventing."""
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    ledger.record(ablation("m-abl"))
    inflated = Claim(
        kind="repeated_query",
        summary="s",
        location=HERE,
        evidence=(Citation(measurement_id="m-1", field="cpu_s", value=2.41),),
        basis=Basis.ABLATION,
        payoff=0.97,
        proof=Proof(measurement_id="m-abl", before=2.41, after=0.52, share_removed=0.97),
    )
    with pytest.raises(FabricatedValueError, match="share_removed"):
        ledger.attest(inflated)


def test_a_nested_measurement_can_be_cited_by_path() -> None:
    """An ablation holds two whole measurements. A claim cites the elapsed time
    of the run before the stub without the ledger needing to know what an
    ablation is."""
    ledger = Ledger()
    ledger.record(ablation("m-abl"))
    claim = Claim(
        kind="repeated_query",
        summary="s",
        location=HERE,
        evidence=(Citation(measurement_id="m-abl", field="before.wall.median", value=2.41),),
    )
    assert ledger.attest(claim).attested_against == ("m-abl",)


def test_a_nested_path_that_does_not_exist_is_refused() -> None:
    ledger = Ledger()
    ledger.record(ablation("m-abl"))
    with pytest.raises(MissingFieldError):
        ledger.attest(cited("m-abl", "before.wall.mean", 2.41))


# ---------------------------------------------------------- what gets recorded


def test_every_kind_of_measurement_can_be_recorded_and_cited() -> None:
    """The ledger knows nothing about the shapes it stores, so a collector added
    later needs no change here."""
    ledger = Ledger()
    ledger.record(bare("m-bare", 1.0))
    ledger.record(ablation("m-abl"))
    ledger.record(
        ProfileMeasurement(
            measurement_id="m-prof",
            command=("py-spy",),
            repeats=1,
            output_digest="0" * 64,
            output_bytes=0,
            instrument="py-spy",
            counts={"samples": 12},
        )
    )
    assert ledger.known == ("m-abl", "m-bare", "m-prof")
    for identifier, field, value in (
        ("m-bare", "cpu_s", 1.0),
        ("m-abl", "share_removed", 0.784),
        ("m-prof", "counts.samples", 12),
    ):
        ledger.attest(cited(identifier, field, value))


def test_a_boolean_is_not_a_number_even_though_python_thinks_it_is() -> None:
    """`output_changed` is `True`, and `True == 1`. A claim citing 1 for it would
    pass an arithmetic comparison and be nonsense."""
    ledger = Ledger()
    ledger.record(ablation("m-abl"))
    with pytest.raises(FabricatedValueError):
        ledger.attest(cited("m-abl", "output_changed", 1))


def test_evidence_errors_are_all_one_family_so_a_caller_can_catch_them() -> None:
    for error in (
        UnknownMeasurementError,
        FabricatedValueError,
        MissingFieldError,
        UnprovenPayoffError,
        ProofIsNotAnAblationError,
        UncitedClaimError,
    ):
        assert issubclass(error, EvidenceError)
