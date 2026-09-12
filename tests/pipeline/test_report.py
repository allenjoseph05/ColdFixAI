"""S-29.3 — what the person sees at the ship gate. ADR 188.

The report is a pure function of the parked state, so everything here builds a
state and reads the answer. What is under test is what it refuses to say: a
finding is never called proven on any basis but an ablation, both halves of that
partition are always printed, and a handover missing something raises rather than
rendering a blank a reader would take for a fact about the patch.
"""

from __future__ import annotations

from typing import Any

import pytest

from coldfix.evidence.adversary import PatchReview, Verdict
from coldfix.evidence.ledger import Basis
from coldfix.evidence.revisions import Comparison, Side
from coldfix.pipeline.report import (
    NotAtTheGateError,
    UnreadableHandoverError,
    awaiting_review,
    report_for,
)
from coldfix.pipeline.state import PipelineState

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-slow()\n+fast()\n"
TEST = "def test_one_query(): assert queries() == 1"


def side(*, stable: bool = True, digest: str = "d") -> Side:
    return Side(
        ran=True,
        output_digest=digest,
        output_bytes=1,
        wall_s=1.0,
        peak_rss_bytes=1,
        stable=stable,
    )


def claim(*, basis: Basis = Basis.ABLATION, summary: str = "161 queries for 40 rows") -> Any:
    payload: dict[str, Any] = {
        "kind": "repeated_query",
        "summary": summary,
        "location": {"file": "app.py", "line": 12, "symbol": "books"},
        "evidence": [{"measurement_id": "m-1", "field": "wall.median", "value": 2.41}],
        "basis": basis.value,
    }
    if basis is Basis.ABLATION:
        payload["payoff"] = 0.78
        payload["proof"] = {
            "measurement_id": "m-1",
            "before": 2.41,
            "after": 0.53,
            "share_removed": 0.78,
        }
    return {"claim": payload, "attested_against": ["m-1"]}


def review(*, verdict: Verdict = Verdict.CLEAN, broke: bool = False) -> dict[str, Any]:
    patched = side(digest="other") if broke else side()
    comparison = Comparison(given=("one",), baseline=side(), patched=patched)
    return PatchReview(verdict=verdict, comparisons=(comparison,), turns=2, notes=()).model_dump(
        mode="json"
    )


def parked(**changes: Any) -> PipelineState:
    """A run stopped in front of a person, with one finding and one patch."""
    state = PipelineState(
        findings={"f-1": claim()},
        resolved={"f-1": {"verdict": "sound", "why": "survived every attack"}},
        coverage={"grounding": "measured", "scan": "submitted"},
        repaired={
            "finding": "f-1",
            "approach": "prefetch the authors",
            "diff": DIFF,
            "test": TEST,
            "measurement_id": "m-c1",
            "share_removed": 0.384,
            "trades": [],
        },
        audited=review(),
    )
    for name, value in changes.items():
        setattr(state, name, value)
    return state


# ------------------------------------------------------------------ the refusals


def test_a_run_that_is_not_at_the_gate_is_refused_by_name() -> None:
    """`ship` clears `repaired`, so a finished run reaches this too. Neither is a
    run awaiting approval, and saying which is not obvious from an empty report."""
    assert awaiting_review(PipelineState()) is False
    with pytest.raises(NotAtTheGateError, match="no patch is parked"):
        report_for(PipelineState())


@pytest.mark.parametrize("missing", ["approach", "diff", "test", "share_removed"])
def test_a_handover_missing_something_raises_rather_than_rendering_a_blank(missing: str) -> None:
    """A person shown an empty section reads it as a fact about the patch rather
    than as a broken report, and that is a reason to reject a good patch."""
    state = parked()
    handover = dict(state.repaired)  # type: ignore[arg-type]
    del handover[missing]
    state.repaired = handover

    with pytest.raises(UnreadableHandoverError, match=missing):
        report_for(state)


def test_an_audit_whose_verdict_its_comparisons_contradict_never_reaches_a_reader() -> None:
    """The reason the audit is validated back into its model instead of read as a
    dict: in the channel a verdict is just a string sitting beside its evidence."""
    state = parked()
    # Forged in the dump rather than the model, because the model refuses to be
    # built this way -- which is the guarantee. A row like this reaches a
    # checkpoint by being written as JSON, never by being constructed.
    forged = review(verdict=Verdict.BROKEN, broke=True)
    forged["verdict"] = Verdict.CLEAN.value
    state.audited = forged

    with pytest.raises(UnreadableHandoverError, match="re-checked against its own comparisons"):
        report_for(state)


def test_a_patch_naming_a_finding_the_run_never_recorded_is_refused() -> None:
    state = parked()
    state.repaired = {**dict(state.repaired), "finding": "f-9"}  # type: ignore[arg-type]

    with pytest.raises(UnreadableHandoverError, match="not in `findings`"):
        report_for(state)


def test_a_parked_patch_with_no_audit_is_a_defect_not_a_skipped_step() -> None:
    state = parked()
    state.audited = None

    with pytest.raises(UnreadableHandoverError, match="runs through"):
        report_for(state)


# ------------------------------------------------------------- proven vs suspected


def test_an_ablated_finding_is_proven_and_a_ratio_is_not() -> None:
    """Both halves asserted. The dangerous direction is drift into the permissive
    one, so a test naming only the proven half would not notice it."""
    state = parked()
    state.findings = {"f-1": claim(), "f-2": claim(basis=Basis.SUSPECTED, summary="a hot site")}

    report = report_for(state)

    assert [item.identifier for item in report.proven] == ["f-1"]
    assert [item.identifier for item in report.suspected] == ["f-2"]


def test_both_halves_are_printed_even_when_one_is_empty() -> None:
    """A report that omitted the empty half would read as though everything in it
    had been proven."""
    rendered = "\n".join(report_for(parked()).lines())

    assert "PROVEN" in rendered
    assert "SUSPECTED" in rendered
    assert "  none" in rendered


def test_shipping_a_patch_for_a_suspected_finding_says_so_before_the_diff() -> None:
    """The prominence rule, inherited from v1's gate: a label under four screens
    of diff is not prominent."""
    state = parked()
    state.findings = {"f-1": claim(basis=Basis.SUSPECTED)}
    lines = report_for(state).lines()

    assert "NEVER ABLATION-PROVEN" in lines[0]
    assert lines.index(next(line for line in lines if "THE DIFF" in line)) > 0


def test_a_proven_finding_carries_no_warning() -> None:
    assert "ABLATION-PROVEN" not in report_for(parked()).lines()[0]


# ----------------------------------------------------------------- what it shows


def test_the_report_carries_the_diff_the_test_and_what_was_attacked() -> None:
    rendered = "\n".join(report_for(parked()).lines())

    assert DIFF in rendered
    assert TEST in rendered
    assert "clean after 2 turn(s), 1 input(s)" in rendered
    assert "38.4% faster" in rendered


def test_coverage_reports_what_it_holds_and_says_when_it_holds_nothing() -> None:
    """`coverage` records how far each phase got, not which workloads were driven.
    Rendering an empty one as a clean bill would claim a check nobody ran."""
    assert "grounding: measured" in "\n".join(report_for(parked()).lines())

    bare = parked()
    bare.coverage = {}
    assert "nothing was recorded" in "\n".join(report_for(bare).lines())


def test_trade_offs_appear_when_there_are_any_and_no_empty_heading_when_not() -> None:
    assert "TRADE-OFFS" not in "\n".join(report_for(parked()).lines())

    state = parked()
    state.repaired = {**dict(state.repaired), "trades": ["cache the queryset"]}  # type: ignore[arg-type]
    rendered = "\n".join(report_for(state).lines())

    assert "TRADE-OFFS" in rendered
    assert "cache the queryset" in rendered


def test_an_attack_that_never_ran_is_not_reported_as_survival() -> None:
    """`unattacked` is its own verdict for this reason, and the report says it in
    words rather than leaving a reader to read an empty list as a pass."""
    state = parked()
    state.audited = PatchReview(verdict=Verdict.UNATTACKED, comparisons=(), turns=4).model_dump(
        mode="json"
    )

    rendered = "\n".join(report_for(state).lines())
    assert "nothing was compared" in rendered


def test_a_broken_patch_still_renders_rather_than_assuming_it_reached_the_gate_clean() -> None:
    """The gate is where a person decides. A report that only knew how to describe
    a clean patch would be useless at the one moment it is read."""
    state = parked()
    state.audited = review(verdict=Verdict.BROKEN, broke=True)

    rendered = "\n".join(report_for(state).lines())
    assert "broken" in rendered
    assert "broke on ['one']" in rendered
