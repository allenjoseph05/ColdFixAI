"""Whether the sweep was wide enough to have answered the question it was asked.

Epic 9, S-9.4. *Checks whether tested scales were large enough to separate linear
from superlinear; flags fits with poor r² or too few points.*

**Nothing here calls a model, and that is the story's first finding.** S-9.4 sits
in an epic of *attacks*, which reads as adversary calls — and `CLAUDE.md` is
explicit: *do not add a model call where a function would do; counting, curve
fitting, stack grouping and byte comparison are code.* Point counts, spans and r²
are arithmetic. An audit that asked a model whether three points were enough
would be paying for an opinion about a number it could compute, and getting a
less reliable answer.

**The threshold is derived, not chosen.** The obvious implementation picks a
round span — *ten times* — and cannot say why. The number falls out of two
figures this project already measured:

- `SUPERLINEAR_ABOVE` is **1.15**: S-1.5 classifies anything above that exponent
  as superlinear, so the gap a sweep must resolve is only **0.15 wide**.
- S-0.4 measured **12%** run-to-run drift on timings.

A power fit is a straight line in log space, so the exponent is
`log(metric ratio) / log(scale ratio)` and a relative error `e` in the metric
becomes an error of `e / ln(span)` in the exponent. Requiring that to be `sigma`
times smaller than the 0.15 gap gives `span >= exp(sigma * e / gap)` — which at
12% noise and 3 sigma is **11**, and is why every sweep in this project's
fixtures uses 10x or 100x rather than 2x.

The consequence worth having is the other direction: a harness with a **certified**
noise floor needs far less span. At S-1.7's floor of 2% the requirement falls to
1.5x. So `required_span` takes the noise rather than assuming it, and a caller
holding a `Certification` passes what it measured.

**Two failures that look alike and are not.** A tight fit over a narrow span is
*confidently wrong*: r² near 1.0 because two points define a line, and an
exponent that means nothing. A loose fit over a wide span is *honestly
uncertain*. Both must be flagged and they are separate objections, because the
remedy differs — widen the sweep against reduce the noise or add points — and an
audit that reported only "inadequate" would send somebody to do the wrong one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.stats import SUPERLINEAR_ABOVE, Fit, Growth

MEASURED_DRIFT = 0.12
"""S-0.4's measured run-to-run drift on timings, used when a caller has no
certified floor of its own. A default with a provenance rather than a guess —
and one a quiet harness should beat, which is why `required_span` takes it as a
parameter."""

SEPARATION_SIGMA = 3.0
"""How many times smaller than the class gap the exponent's uncertainty must be.

Three because this decides whether a finding is reported, and a boundary call at
one sigma is a coin flip dressed as a measurement."""

MINIMUM_POINTS_TO_TRUST = 4
"""One more than the instrument's own minimum, and the difference is the point.

S-3.2 refuses a sweep below three because two points define a line through
themselves. Three is what it takes to *fit*; four is what it takes to *check* —
at three, a power fit has one residual degree of freedom, so one outlier moves
the exponent without moving r² much, and there is no point that can be dropped
and re-fitted as a test. An audit whose bar equals the instrument's bar is not
auditing anything."""

MINIMUM_R_SQUARED = 0.90
"""Below this the power law is a poor description of the data, so the exponent it
yields describes a curve the measurements do not follow. Independent of span: a
wide sweep with a bad fit is still a bad fit."""


class Inadequacy(StrEnum):
    """Why a sweep cannot support the growth claim made from it.

    Separate members rather than one *inadequate*, for S-3.1's reason: the
    reader's next action differs for each, and collapsing them sends somebody to
    do the wrong thing.
    """

    TOO_FEW_POINTS = "too few scale points to check the fit rather than merely make one"
    SPAN_TOO_NARROW = "the scales are too close together to separate linear from superlinear"
    FIT_TOO_POOR = "the power law does not describe these measurements"
    NO_EXPONENT = "no exponent could be fitted, so no growth class was established"


def exponent_uncertainty(*, span: float, relative_noise: float) -> float:
    """How far the fitted exponent can be wrong, given the span and the noise.

    `e / ln(span)`: a power fit is linear in log space, so the exponent is
    `log(metric ratio) / log(scale ratio)`, and relative error in the metric
    divides by the log of the span.

    Raises:
        ValueError: a span of one or less is no sweep at all, and `ln(1)` is zero
            — the uncertainty would be infinite, which is true and unhelpful to
            divide by.
    """
    if span <= 1.0:
        message = (
            f"a span of {span} is not a sweep: every point was taken at the same scale, so the "
            "exponent is not merely uncertain, it is undefined"
        )
        raise ValueError(message)
    return relative_noise / math.log(span)


def required_span(
    relative_noise: float = MEASURED_DRIFT, *, sigma: float = SEPARATION_SIGMA
) -> float:
    """The smallest span that separates linear from superlinear at this noise.

    `exp(sigma * e / gap)`, where the gap is `SUPERLINEAR_ABOVE - 1`. Derived
    rather than chosen, which is what lets a caller with a certified noise floor
    ask for less: 12% drift needs 11x, and S-1.7's 2% floor needs 1.5x.
    """
    gap = SUPERLINEAR_ABOVE - 1.0
    return math.exp(sigma * relative_noise / gap)


@dataclass(frozen=True)
class ScaleAudit:
    """What the sweep can and cannot support, with the numbers behind it."""

    scales: tuple[float, ...]
    span: float
    uncertainty: float | None
    """`None` when the span is degenerate, in which case there is no exponent to
    be uncertain about."""

    required: float
    objections: tuple[Inadequacy, ...]

    @property
    def adequate(self) -> bool:
        return not self.objections

    def describe(self) -> str:
        if self.adequate:
            return (
                f"Scales {list(self.scales)} span {self.span:.3g}x, which clears the "
                f"{self.required:.3g}x needed at this noise; the exponent is determined to "
                f"±{self.uncertainty:.3g} against a class gap of {SUPERLINEAR_ABOVE - 1:.2g}."
            )
        lines = [
            f"Scales {list(self.scales)} span {self.span:.3g}x. "
            "This sweep does not support a growth claim:"
        ]
        lines.extend(f"  - {item.value}" for item in self.objections)
        if Inadequacy.SPAN_TOO_NARROW in self.objections:
            lines.append(
                f"  A span of {self.required:.3g}x is needed at this noise level. Widening the "
                "sweep is the remedy; more points at the same scales will not help."
            )
        if Inadequacy.FIT_TOO_POOR in self.objections:
            lines.append(
                "  The fit is the problem rather than the range — reduce the noise or add "
                "points. A wider sweep would fit the same curve just as badly."
            )
        return "\n".join(lines)


def audit_scales(
    scales: Sequence[float],
    fit: Fit,
    *,
    relative_noise: float = MEASURED_DRIFT,
) -> ScaleAudit:
    """Attack the sweep behind a growth claim. AC 1 and AC 2.

    Raises:
        ValueError: no scales at all, which is not a narrow sweep but the absence
            of one.
    """
    if not scales:
        message = "there are no scale points here, so there is no sweep to audit"
        raise ValueError(message)

    ordered = tuple(sorted(float(scale) for scale in scales))
    span = ordered[-1] / ordered[0] if ordered[0] > 0 else math.inf
    needed = required_span(relative_noise)

    objections: list[Inadequacy] = []
    if len(set(ordered)) < MINIMUM_POINTS_TO_TRUST:
        objections.append(Inadequacy.TOO_FEW_POINTS)

    uncertainty: float | None = None
    if span <= 1.0:
        objections.append(Inadequacy.SPAN_TOO_NARROW)
    else:
        uncertainty = exponent_uncertainty(span=span, relative_noise=relative_noise)
        if span < needed:
            objections.append(Inadequacy.SPAN_TOO_NARROW)

    # **Checked in this order deliberately.** A missing exponent is not a poor
    # fit — S-1.5 sets `exponent`, `power_r_squared` and `growth` to `None`
    # together when a power law could not be fitted at all — so reading r² first
    # would report *the power law does not describe these measurements* about a
    # power law nobody managed to fit.
    if fit.exponent is None or fit.power_r_squared is None:
        objections.append(Inadequacy.NO_EXPONENT)
    elif fit.power_r_squared < MINIMUM_R_SQUARED:
        objections.append(Inadequacy.FIT_TOO_POOR)

    return ScaleAudit(
        scales=ordered,
        span=span,
        uncertainty=uncertainty,
        required=needed,
        objections=tuple(objections),
    )


def resolves_growth(audit: ScaleAudit, growth: Growth) -> bool:
    """Whether this sweep can support *that particular* claim.

    A `CONSTANT` verdict needs less than a `SUPERLINEAR` one: the gap between
    constant and linear is the whole of 1.0, so a sweep too narrow to separate
    linear from superlinear may still be wide enough to show that a metric does
    not grow at all. Reporting such a sweep as unusable would throw away the
    exclusions `00-BRIEF.md` §9 ships as answers.
    """
    if audit.uncertainty is None:
        return False
    blocking = [item for item in audit.objections if item is not Inadequacy.SPAN_TOO_NARROW]
    if blocking:
        return False
    if growth is Growth.SUPERLINEAR or growth is Growth.LINEAR:
        return audit.span >= audit.required
    # CONSTANT against LINEAR is a gap of 1.0 rather than 0.15.
    return audit.uncertainty * SEPARATION_SIGMA < 1.0
