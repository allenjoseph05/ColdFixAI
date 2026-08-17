"""S-9.4 — whether the sweep was wide enough to have answered the question.

Two things get attacked hardest. The threshold has to be **derived** rather than
chosen, or the audit is one arbitrary number judging another; and the module has
to reject a bad sweep *and* accept a good one, because an auditor that objects to
everything is as useless as one that objects to nothing and much harder to
notice.
"""

from __future__ import annotations

import inspect
import math

import pytest

from coldfix.audit import scales
from coldfix.audit.scales import (
    MEASURED_DRIFT,
    MINIMUM_POINTS_TO_TRUST,
    MINIMUM_R_SQUARED,
    SEPARATION_SIGMA,
    Inadequacy,
    audit_scales,
    exponent_uncertainty,
    required_span,
    resolves_growth,
)
from coldfix.bench.stats import CONSTANT_BELOW, SUPERLINEAR_ABOVE, Fit, Growth
from coldfix.primitives.scaling import MINIMUM_SCALE_POINTS


def a_fit(*, exponent: float | None = 2.0, r_squared: float | None = 0.99) -> Fit:
    """A power fit, varying only what a test is about."""
    return Fit(
        slope=1.0,
        intercept=0.0,
        linear_r_squared=0.99,
        exponent=exponent,
        power_r_squared=r_squared,
        growth=None if exponent is None else Growth.SUPERLINEAR,
        constant_below=CONSTANT_BELOW,
        superlinear_above=SUPERLINEAR_ABOVE,
    )


# ============================ the threshold is derived from measured figures


def test_the_required_span_falls_out_of_two_numbers_this_project_measured() -> None:
    """**Not a chosen constant.** `SUPERLINEAR_ABOVE` is 1.15, so the gap a sweep
    must resolve is 0.15 wide; S-0.4 measured 12% drift. A power fit is linear in
    log space, so `span >= exp(sigma * noise / gap)` — and that is 11x, which is
    why this project's fixtures sweep 10x and 100x rather than 2x."""
    gap = SUPERLINEAR_ABOVE - 1.0

    assert gap == pytest.approx(0.15)
    assert required_span(MEASURED_DRIFT) == pytest.approx(math.exp(SEPARATION_SIGMA * 0.12 / gap))
    assert required_span(MEASURED_DRIFT) == pytest.approx(11.02, abs=0.01)


def test_a_quieter_harness_needs_less_span() -> None:
    """**The consequence worth having**, and the reason the noise is a parameter:
    a caller holding S-1.7's certified floor asks for what it actually needs. At
    2% the requirement falls from 11x to 1.5x."""
    assert required_span(0.02) == pytest.approx(1.49, abs=0.01)
    assert required_span(0.02) < required_span(0.05) < required_span(MEASURED_DRIFT)


def test_the_uncertainty_is_the_noise_divided_by_the_log_of_the_span() -> None:
    assert exponent_uncertainty(span=10, relative_noise=0.12) == pytest.approx(0.052, abs=0.001)
    assert exponent_uncertainty(span=100, relative_noise=0.12) == pytest.approx(0.026, abs=0.001)


def test_a_two_times_sweep_cannot_separate_the_classes_at_measured_noise() -> None:
    """The number that makes the threshold non-arbitrary: at 12% noise a 2x sweep
    determines the exponent to ±0.17, and the whole gap between linear and
    superlinear is 0.15. The sweep cannot tell them apart at all."""
    uncertainty = exponent_uncertainty(span=2, relative_noise=MEASURED_DRIFT)

    assert uncertainty > SUPERLINEAR_ABOVE - 1.0


def test_a_sweep_with_no_span_is_undefined_rather_than_merely_uncertain() -> None:
    with pytest.raises(ValueError, match="not a sweep"):
        exponent_uncertainty(span=1.0, relative_noise=0.12)


# ======================================================== AC 1 and AC 2: objections


def test_a_narrow_sweep_is_flagged() -> None:
    audit = audit_scales([100, 120, 150, 200], a_fit())

    assert Inadequacy.SPAN_TOO_NARROW in audit.objections
    assert not audit.adequate


def test_a_wide_sweep_with_a_good_fit_is_accepted() -> None:
    """**The control**, and the one that stops this being an auditor that objects
    to everything — which passes every negative test here while making the whole
    epic a machine for rejecting sound findings."""
    audit = audit_scales([10, 100, 1000, 10_000], a_fit())

    assert audit.adequate
    assert audit.objections == ()
    assert "clears the" in audit.describe()


def test_a_poor_fit_is_flagged_however_wide_the_sweep() -> None:
    """Independent of span: a wide sweep with a bad fit is still a bad fit."""
    audit = audit_scales([10, 100, 1000, 10_000], a_fit(r_squared=0.4))

    assert Inadequacy.FIT_TOO_POOR in audit.objections
    assert Inadequacy.SPAN_TOO_NARROW not in audit.objections


def test_three_points_is_the_instruments_minimum_and_below_the_audits() -> None:
    """**An audit whose bar equals the instrument's bar is not auditing
    anything.** S-3.2 refuses below three because two points define a line; three
    is what it takes to *fit* and four is what it takes to *check* — at three
    there is no point that can be dropped and re-fitted as a test."""
    assert MINIMUM_POINTS_TO_TRUST == MINIMUM_SCALE_POINTS + 1

    audit = audit_scales([10, 100, 1000], a_fit())

    assert Inadequacy.TOO_FEW_POINTS in audit.objections


def test_repeated_scales_do_not_count_as_separate_points() -> None:
    """Four measurements at three distinct scales is a three-point sweep. Counting
    the measurements would let a caller clear the bar by re-running one point."""
    audit = audit_scales([10, 10, 100, 1000], a_fit())

    assert Inadequacy.TOO_FEW_POINTS in audit.objections


def test_a_missing_exponent_is_not_reported_as_a_poor_fit() -> None:
    """**Order matters and this is why.** S-1.5 sets `exponent`,
    `power_r_squared` and `growth` to `None` *together* when a power law could not
    be fitted at all, so reading r² first would report *the power law does not
    describe these measurements* about a power law nobody managed to fit."""
    audit = audit_scales([10, 100, 1000, 10_000], a_fit(exponent=None, r_squared=None))

    assert Inadequacy.NO_EXPONENT in audit.objections
    assert Inadequacy.FIT_TOO_POOR not in audit.objections


def test_every_objection_that_applies_is_reported() -> None:
    """A sweep can be short *and* narrow *and* badly fitted, and a reader fixing
    only the first would run it again and fail again."""
    audit = audit_scales([100, 110], a_fit(r_squared=0.2))

    assert Inadequacy.TOO_FEW_POINTS in audit.objections
    assert Inadequacy.SPAN_TOO_NARROW in audit.objections
    assert Inadequacy.FIT_TOO_POOR in audit.objections


def test_no_scales_at_all_is_the_absence_of_a_sweep() -> None:
    with pytest.raises(ValueError, match="no sweep to audit"):
        audit_scales([], a_fit())


# =============================== the two failures that look alike and are not


def test_a_narrow_sweep_is_told_to_widen_and_a_bad_fit_is_not() -> None:
    """**The distinction the objections exist to preserve.** A tight fit over a
    narrow span is confidently wrong; a loose fit over a wide span is honestly
    uncertain. The remedies are opposite, and an audit reporting only
    *inadequate* would send somebody to do the wrong one."""
    narrow = audit_scales([100, 120, 150, 200], a_fit(r_squared=0.999)).describe()
    loose = audit_scales([10, 100, 1000, 10_000], a_fit(r_squared=0.3)).describe()

    assert "Widening the sweep is the remedy" in narrow
    assert "more points at the same scales will not help" in narrow

    assert "reduce the noise or add points" in loose
    assert "Widening the sweep" not in loose


def test_a_narrow_sweep_can_fit_perfectly_and_still_establish_nothing() -> None:
    """The failure that reads as success: r² of 0.999 over a 1.2x span. The fit is
    excellent and the exponent it yields is meaningless."""
    audit = audit_scales([100, 105, 110, 120], a_fit(r_squared=0.999))

    assert Inadequacy.FIT_TOO_POOR not in audit.objections
    assert not audit.adequate


# ============ a narrow sweep can still support the weaker claim, and should


def test_a_sweep_too_narrow_for_superlinear_can_still_show_a_metric_is_flat() -> None:
    """**Refusing this would throw away exclusions.** The gap between constant and
    linear is the whole of 1.0, not 0.15, so a sweep that cannot separate linear
    from superlinear may still be plenty to show a metric does not grow — and
    `00-BRIEF.md` §9 ships that as an answer."""
    audit = audit_scales([100, 150, 250, 400], a_fit(exponent=0.02, r_squared=0.98))

    assert Inadequacy.SPAN_TOO_NARROW in audit.objections
    assert resolves_growth(audit, Growth.CONSTANT)
    assert not resolves_growth(audit, Growth.SUPERLINEAR)


def test_a_wide_sweep_supports_the_stronger_claim_too() -> None:
    audit = audit_scales([10, 100, 1000, 10_000], a_fit())

    assert resolves_growth(audit, Growth.SUPERLINEAR)
    assert resolves_growth(audit, Growth.CONSTANT)


def test_a_badly_fitted_sweep_supports_no_claim_at_all() -> None:
    """A poor fit blocks every verdict, because the objection is about the curve
    rather than about the range."""
    audit = audit_scales([10, 100, 1000, 10_000], a_fit(r_squared=0.2))

    assert not resolves_growth(audit, Growth.CONSTANT)
    assert not resolves_growth(audit, Growth.SUPERLINEAR)


# ======================================================== the thresholds are stated


def test_the_fit_threshold_is_named_rather_than_inlined() -> None:
    assert MINIMUM_R_SQUARED == 0.90
    assert audit_scales([10, 100, 1000, 10_000], a_fit(r_squared=0.9)).adequate
    assert not audit_scales([10, 100, 1000, 10_000], a_fit(r_squared=0.89)).adequate


def test_nothing_here_needs_a_model() -> None:
    """`CLAUDE.md`: do not add a model call where a function would do — counting
    and curve fitting are code. S-9.4 sits in an epic of *attacks*, which reads as
    adversary calls, and this one is arithmetic."""
    source = inspect.getsource(scales)

    assert "ModelClient" not in source
    assert "Session" not in source
    assert "complete(" not in source
