"""S-28.3 — a ledger a resumed run can re-check its findings against. ADR 183.

`audit_finding`'s first attack re-attests every cited number against the ledger as
it stands. That is right: a `Finding` survives a rewind and the measurements
behind it may not. It is also why a resumed run needs its ledger back -- with an
empty one the attack fails *fatally*, and `unsound` is the one verdict that does
not send the run back for more evidence.
"""

from __future__ import annotations

import pytest

from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement, Mode, Spread
from coldfix.evidence.audit import Verdict, attack
from coldfix.evidence.ledger import (
    Basis,
    Citation,
    Claim,
    EvidenceError,
    Ledger,
    Location,
    Proof,
    UnknownMeasurementError,
)

MEDIAN = 2.41034567891


def bare(identifier: str, median: float = MEDIAN) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "app.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=12,
        wall=Spread(median=median, low=median, high=median),
        cpu_s=median,
        mode=Mode.COMPUTING,
        peak_rss_bytes=81_234,
        read_bytes=0,
        write_bytes=0,
    )


def claim(identifier: str = "m-1", value: float = MEDIAN) -> Claim:
    return Claim(
        kind="slow_path",
        summary="the driver's one call is computing",
        location=Location(file="app.py", line=3, symbol="books"),
        evidence=(Citation(measurement_id=identifier, field="wall.median", value=value),),
    )


def recorded() -> Ledger:
    ledger = Ledger()
    ledger.record(bare("m-1"))
    ledger.record(bare("m-2", 0.52))
    return ledger


def test_what_a_checkpoint_carries_restores_a_ledger_that_answers_the_same() -> None:
    original = recorded()
    finding = original.attest(claim())

    resumed = Ledger()
    resumed.restore(original.entries())

    assert resumed.known == original.known
    assert resumed.attest(finding.claim) == finding
    assert resumed.recorded("m-1") == original.recorded("m-1")


def test_a_finding_survives_its_audit_after_a_resume() -> None:
    """The failure this exists to prevent: a good finding judged `unsound` by a
    run that simply forgot what it measured."""
    original = recorded()
    finding = original.attest(claim())

    empty = Ledger()
    assert attack(finding, ledger=empty).verdict is Verdict.UNSOUND, "the failure, reproduced"

    resumed = Ledger()
    resumed.restore(original.entries())
    assert attack(finding, ledger=resumed).verdict is not Verdict.UNSOUND


def test_a_proof_is_still_known_to_be_an_ablation_after_a_restore() -> None:
    """`attest` checks the *kind* of the measurement a proof cites, so the kind
    has to travel with it -- the fields alone would make an ablation unrecognisable."""
    ledger = Ledger()
    before, after = bare("m-before"), bare("m-after", 0.52)
    ledger.record(before)
    ledger.record(after)
    ledger.record(
        AblationMeasurement(
            measurement_id="m-abl",
            symbol="books",
            file="app.py",
            line=3,
            before=before,
            after=after,
            share_removed=0.784,
            output_changed=True,
        )
    )
    proven = Claim(
        kind="slow_path",
        summary="removing it removes the cost",
        location=Location(file="app.py", line=3, symbol="books"),
        evidence=(Citation(measurement_id="m-abl", field="share_removed", value=0.784),),
        basis=Basis.ABLATION,
        payoff=0.784,
        proof=Proof(measurement_id="m-abl", before=MEDIAN, after=0.52, share_removed=0.784),
    )
    finding = ledger.attest(proven)

    resumed = Ledger()
    resumed.restore(ledger.entries())
    assert resumed.attest(proven) == finding


def test_restoring_adds_to_what_is_there_and_does_not_replace_it() -> None:
    live = Ledger()
    live.record(bare("m-live"))
    live.restore(recorded().entries())
    assert live.known == ("m-1", "m-2", "m-live")


def test_an_unknown_measurement_is_still_unknown_after_a_restore() -> None:
    """Restoring is not a way to make any citation pass."""
    resumed = Ledger()
    resumed.restore(recorded().entries())
    with pytest.raises(UnknownMeasurementError):
        resumed.attest(claim("m-never"))


@pytest.mark.parametrize(
    "entry",
    [
        {"kind": "BareMeasurement", "fields": {}},
        {"measurement_id": "m-1", "fields": {}},
        {"measurement_id": "m-1", "kind": "BareMeasurement"},
        {"measurement_id": "m-1", "kind": "BareMeasurement", "fields": "not a mapping"},
        "not an entry at all",
    ],
)
def test_a_record_the_ledger_cannot_read_is_refused_rather_than_skipped(entry: object) -> None:
    """A silently skipped record is a citation that fails later, at the audit,
    reported as a finding nothing supports."""
    with pytest.raises(EvidenceError):
        Ledger().restore([entry])  # type: ignore[list-item]
