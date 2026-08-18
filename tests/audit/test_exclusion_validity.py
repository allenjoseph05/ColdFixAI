"""S-9.2 — whether a thing was ruled out under conditions broad enough to rule it out.

The load-bearing test here is the **control**. An auditor that objected to every
exclusion satisfies both acceptance criteria and makes `00-BRIEF.md` §9's *null
results are valid output* unreachable — every proven negative rejected on the
grounds that it might not hold somewhere nobody looked. So each objection has a
case that must not raise it.
"""

from __future__ import annotations

import inspect

from coldfix.audit import exclusions
from coldfix.audit.exclusions import (
    ExclusionAudit,
    Narrowness,
    audit_all,
    audit_exclusion,
    report,
)
from coldfix.bench.stats import CONSTANT_BELOW, SUPERLINEAR_ABOVE, Fit, Growth
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.primitives.scaling import Distribution

PLATFORM = "x86_64-linux"
WIDE = [10, 100, 1000, 10_000]


def conditions(
    *,
    shape: str | list[str] = Distribution.UNIFORM.value,
    concurrency: float | list[float] = 1,
    platform: str = PLATFORM,
    scales: list[float] | None = None,
) -> Conditions:
    return Conditions.of(
        fixture_shape=shape,
        platform=platform,
        concurrency=concurrency,
        scales=scales if scales is not None else WIDE,
    )


def an_exclusion(**overrides: object) -> Exclusion:
    log = ExperimentLog()
    experiment = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000, 10000])",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7 across a 1000x sweep",
    )
    return Exclusion(experiment=experiment, conditions=conditions(**overrides))  # type: ignore[arg-type]


def a_fit(*, exponent: float = 0.02, r_squared: float = 0.98) -> Fit:
    return Fit(
        slope=0.0,
        intercept=7.0,
        linear_r_squared=0.99,
        exponent=exponent,
        power_r_squared=r_squared,
        growth=Growth.CONSTANT,
        constant_below=CONSTANT_BELOW,
        superlinear_above=SUPERLINEAR_ABOVE,
    )


# ================================================= AC 2: what was not varied


def test_a_uniform_only_exclusion_is_flagged_as_the_blindest_shape() -> None:
    """**F3's worked example, attacked.** S-3.3 exists because `Σ k²` is minimized
    exactly when every parent is equal, so for any per-parent cost the uniform
    fixture is the *provably blindest* one. An exclusion established only there
    was established under the shape least able to reveal what it ruled out."""
    audit = audit_exclusion(an_exclusion(shape=Distribution.UNIFORM.value))

    assert Narrowness.UNIFORM_ONLY in audit.objections
    assert "provably blindest" in Narrowness.UNIFORM_ONLY.value


def test_a_long_tail_only_exclusion_gets_the_weaker_objection() -> None:
    """**The asymmetry is proved rather than felt.** Long tail is also one shape,
    and it is the deliberate worst case — so the objection applies, but not the
    uniform one. Collapsing the two would tell a reader who already used the
    hardest fixture that they used the blindest."""
    audit = audit_exclusion(an_exclusion(shape=Distribution.LONG_TAIL.value))

    assert Narrowness.SINGLE_SHAPE in audit.objections
    assert Narrowness.UNIFORM_ONLY not in audit.objections


def test_an_exclusion_swept_across_every_shape_is_not_flagged_for_shape() -> None:
    """The control. S-3.3's `compare_shapes` sweeps all three in one experiment,
    and an audit that objected anyway would be objecting to the instrument doing
    exactly what it was built to do."""
    every = [item.value for item in Distribution]

    audit = audit_exclusion(an_exclusion(shape=every))

    assert Narrowness.UNIFORM_ONLY not in audit.objections
    assert Narrowness.SINGLE_SHAPE not in audit.objections


def test_a_serial_exclusion_is_flagged() -> None:
    """An exclusion established at concurrency 1 says nothing about contention,
    which is what S-3.12 and S-3.13 exist to measure."""
    audit = audit_exclusion(an_exclusion(concurrency=1))

    assert Narrowness.SERIAL_ONLY in audit.objections


def test_an_exclusion_established_under_load_is_not_flagged_for_concurrency() -> None:
    """The control."""
    audit = audit_exclusion(an_exclusion(concurrency=[1, 8, 32]))

    assert Narrowness.SERIAL_ONLY not in audit.objections


def test_a_narrow_sweep_is_flagged_by_delegating_to_s94() -> None:
    """The scale axis is S-9.4's question, and asking it twice would be two
    statements of one rule — the shape this project keeps refusing."""
    audit = audit_exclusion(an_exclusion(scales=[100, 110, 120, 130]), fit=a_fit())

    assert Narrowness.NARROW_SCALE_SPAN in audit.objections
    assert audit.scales is not None
    assert not audit.scales.adequate


def test_a_wide_sweep_is_not_flagged_for_scale() -> None:
    audit = audit_exclusion(an_exclusion(scales=WIDE), fit=a_fit())

    assert Narrowness.NARROW_SCALE_SPAN not in audit.objections
    assert audit.scales is not None
    assert audit.scales.adequate


def test_an_exclusion_with_no_sweep_behind_it_is_not_faulted_for_lacking_one() -> None:
    """**Not every rejection came from a sweep.** An ablation that removed nothing
    rules something out with no exponent anywhere in it, and inventing a fit to
    judge would be auditing a curve nobody drew. `None` means *not audited*, which
    S-3.1 distinguishes from *passed*."""
    audit = audit_exclusion(an_exclusion(scales=[100, 110]))

    assert Narrowness.NARROW_SCALE_SPAN not in audit.objections
    assert audit.scales is None


# ============================ actionable against inherent, because remedies differ


def test_a_single_platform_is_recorded_and_does_not_make_an_exclusion_inadequate() -> None:
    """**You cannot demand a second architecture.** Reporting it beside the
    fixable objections would spend the reader's attention on the one item they
    can do nothing about — so it is a bound, which is what `00-BRIEF.md` already
    requires an exclusion to carry."""
    audit = audit_exclusion(
        an_exclusion(shape=[item.value for item in Distribution], concurrency=[1, 8])
    )

    assert Narrowness.SINGLE_PLATFORM in audit.objections
    assert audit.adequate
    assert not Narrowness.SINGLE_PLATFORM.actionable


def test_every_other_objection_is_actionable_and_names_its_remedy() -> None:
    for item in Narrowness:
        assert item.remedy
        if item is not Narrowness.SINGLE_PLATFORM:
            assert item.actionable


def test_the_uniform_remedy_points_at_the_tool_that_reopens_it() -> None:
    """S-8.8 closes this loop: reseeding to a skewed fixture moves the shape
    condition, which reopens the exclusion automatically via S-8.5."""
    assert "reseed" in Narrowness.UNIFORM_ONLY.remedy
    assert "S-8.8" in Narrowness.UNIFORM_ONLY.remedy


def test_the_report_separates_what_can_be_fixed_from_what_must_be_recorded() -> None:
    """**And it prints the remedy, which a sabotage had to point out.**

    The first version asserted only that each objection *has* a remedy and that
    the two sections exist — so dropping the remedy from the rendering changed
    nothing. Testing the data and not the rendering is the same gap in a
    different place: the reader gets the report, not the enum.
    """
    described = audit_exclusion(an_exclusion(), fit=a_fit()).describe()

    assert "Not varied, and fixable:" in described
    assert "Bounds on this exclusion, recorded rather than fixable:" in described
    assert "remedy:" in described
    assert Narrowness.UNIFORM_ONLY.remedy in described
    assert Narrowness.SERIAL_ONLY.remedy in described


# ================================================ the bound this audit cannot cross


def test_the_report_says_it_cannot_judge_whether_the_unvaried_axis_mattered() -> None:
    """The honest limit. This module sees the conditions and cannot see whether
    the unvaried axis was relevant to *this* hypothesis — that is S-9.5's
    alternative-explanation attack, which needs a model."""
    described = audit_exclusion(an_exclusion()).describe()

    assert "Whether an unvaried axis mattered to this hypothesis is a judgement" in described


def test_nothing_here_needs_a_model() -> None:
    """Two of Epic 9's seven attacks turn out to be arithmetic."""
    source = inspect.getsource(exclusions)

    assert "ModelClient" not in source
    assert "complete(" not in source


# ======================================== AC 1: adequate conditions are recognised


def test_a_thoroughly_established_exclusion_is_reported_as_adequate() -> None:
    """**The control that makes this story worth anything.** An auditor objecting
    to every exclusion would satisfy both AC while making §9's *null results are
    valid output* unreachable."""
    audit = audit_exclusion(
        an_exclusion(shape=[item.value for item in Distribution], concurrency=[1, 8, 32]),
        fit=a_fit(),
    )

    assert audit.adequate
    assert "Nothing was left unvaried" not in audit.describe()  # the platform bound remains


def test_an_exclusion_with_no_narrowness_at_all_says_so() -> None:
    """Reachable only if every axis was varied, platform included — which is the
    honest shape of *this audit found nothing*."""
    audit = ExclusionAudit(exclusion=an_exclusion(), objections=(), scales=None)

    assert "Nothing was left unvaried" in audit.describe()


def test_every_objection_that_applies_is_reported() -> None:
    audit = audit_exclusion(an_exclusion(scales=[100, 110, 120, 130]), fit=a_fit())

    assert Narrowness.UNIFORM_ONLY in audit.objections
    assert Narrowness.SERIAL_ONLY in audit.objections
    assert Narrowness.NARROW_SCALE_SPAN in audit.objections
    assert Narrowness.SINGLE_PLATFORM in audit.objections


def test_a_register_of_exclusions_is_audited_together() -> None:
    audits = audit_all([an_exclusion(), an_exclusion(concurrency=[1, 8])])

    assert len(audits) == 2
    assert "2 exclusion(s) examined" in report(audits)


def test_nothing_ruled_out_has_no_preconditions_to_attack() -> None:
    assert "no preconditions to attack" in report([])


def test_a_fit_is_matched_to_its_own_experiment() -> None:
    """Keyed by experiment index, because auditing exclusion 2's span against
    exclusion 1's fit would be judging a sweep nobody ran."""
    first = an_exclusion()
    audits = audit_all([first], fits={first.experiment.index: a_fit()})

    assert audits[0].scales is not None
