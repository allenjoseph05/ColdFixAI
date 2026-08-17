"""Whether a thing was ruled out under conditions broad enough to have ruled it out.

Epic 9, S-9.2. *Checks whether ruled-out hypotheses were ruled out under adequate
conditions; flags exclusions whose preconditions were too narrow.*

This is S-8.5's machinery turned on itself. That story made every exclusion carry
the conditions it holds under so it *could* be reopened; this one reads those
conditions and asks whether they were ever wide enough to establish anything.

**Mostly no model call, and the exception is named.** Like S-9.4, the checkable
part is arithmetic: which axes were varied, and by how much. What this cannot do
is judge whether an unvaried axis was *relevant to this particular hypothesis* —
that is semantic, and it belongs to S-9.5's alternative-explanation attack. So
this flags **what was not varied** and says so in those terms, rather than
claiming to know what mattered.

**Uniform-only is a sharper objection than single-shape, and the asymmetry is
proved rather than felt.** S-3.3 exists because `Σ k²` is minimized exactly when
every parent has the same number of children — so for **any** per-parent cost the
uniform fixture is the *provably blindest* one. An exclusion established only
under `uniform` was established under the shape least able to reveal the thing it
ruled out. An exclusion established only under `long_tail` is also single-shape,
but long tail is the deliberate worst case, so the same objection does not apply
with the same force. Two members rather than one, for S-3.1's reason: the
reader's next action differs.

**Some narrowness is actionable and some is not, and reporting them alike would
waste somebody's time.** A uniform-only exclusion is reopened by S-8.8's reseed;
a serial-only one by raising concurrency; a narrow sweep by widening it. A
single-platform exclusion is none of those — you cannot reasonably demand the
run happen on two architectures — so it is **stated as a bound rather than
flagged as a defect**, which is `00-BRIEF.md`'s *exclusions carry their
preconditions* read as a reporting rule.

**The control is the load-bearing half.** An auditor that objected to every
exclusion would satisfy both acceptance criteria while making `00-BRIEF.md` §9's
*null results are valid output* unreachable — every proven negative would be
rejected on the grounds that it might not hold somewhere nobody looked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.audit.scales import ScaleAudit, audit_scales
from coldfix.bench.stats import Fit
from coldfix.diagnosis.exclusions import Dimension, Exclusion
from coldfix.primitives.scaling import Distribution

SERIAL = 1.0
"""The concurrency a workload is driven at unless a load primitive says
otherwise. An exclusion established here says nothing about contention, which is
what S-3.12 and S-3.13 exist to measure."""


class Narrowness(StrEnum):
    """An axis an exclusion was never varied along.

    Phrased as *what was not varied* rather than *what was missed*, because this
    module can see the conditions and cannot see whether the unvaried axis
    mattered to the hypothesis. Claiming the second would be an opinion wearing a
    computation's clothes.
    """

    UNIFORM_ONLY = (
        "established only under a uniform fixture, which is the provably blindest "
        "shape for any per-parent cost"
    )
    SINGLE_SHAPE = "established under one fixture shape, so shape-dependent costs were not tested"
    SERIAL_ONLY = (
        "established at concurrency 1, so nothing contended and nothing was measured under load"
    )
    NARROW_SCALE_SPAN = "the scales were too close together to have established a growth claim"
    SINGLE_PLATFORM = "established on one platform, which is a bound rather than a fixable gap"

    @property
    def actionable(self) -> bool:
        """Whether somebody can widen this, or only record it.

        `SINGLE_PLATFORM` is the one that cannot be acted on: demanding a second
        architecture is not a remedy anybody can apply, so reporting it beside the
        three that *are* fixable would waste the reader's attention on the one
        item they cannot do anything about.
        """
        return self is not Narrowness.SINGLE_PLATFORM

    @property
    def remedy(self) -> str:
        return _REMEDIES[self]


_REMEDIES: dict[Narrowness, str] = {
    Narrowness.UNIFORM_ONLY: (
        "reseed to a skewed fixture (S-8.8) — which reopens this exclusion automatically, "
        "because the shape it was recorded under will have moved"
    ),
    Narrowness.SINGLE_SHAPE: "sweep the remaining shapes with scaling.shape",
    Narrowness.SERIAL_ONLY: "re-run under load, or record that this exclusion is a serial one",
    Narrowness.NARROW_SCALE_SPAN: "widen the sweep; more points at the same scales will not help",
    Narrowness.SINGLE_PLATFORM: (
        "none — record it. `00-BRIEF.md` requires an exclusion to carry its preconditions, and "
        "the platform is one of them whether or not anybody can vary it"
    ),
}


@dataclass(frozen=True)
class ExclusionAudit:
    """What an exclusion did and did not establish."""

    exclusion: Exclusion
    objections: tuple[Narrowness, ...]
    scales: ScaleAudit | None
    """S-9.4's answer for the scale axis, when a fit was available to judge. `None`
    means the scale span was not audited rather than that it passed — the
    distinction S-3.1 makes between *no* and *not known*."""

    @property
    def adequate(self) -> bool:
        """Whether anything actionable was left untested.

        A single platform does not make an exclusion inadequate; it makes it
        conditional, which it already was.
        """
        return not [item for item in self.objections if item.actionable]

    def describe(self) -> str:
        head = (
            f"{self.exclusion.hypothesis!r} was ruled out by experiment "
            f"{self.exclusion.experiment.index} under {self.exclusion.conditions.describe()}."
        )
        if not self.objections:
            return f"{head}\n  Nothing was left unvaried that this audit can see."

        lines = [head]
        actionable = [item for item in self.objections if item.actionable]
        inherent = [item for item in self.objections if not item.actionable]
        if actionable:
            lines.append("  Not varied, and fixable:")
            lines.extend(f"    - {item.value}\n      remedy: {item.remedy}" for item in actionable)
        if inherent:
            lines.append("  Bounds on this exclusion, recorded rather than fixable:")
            lines.extend(f"    - {item.value}" for item in inherent)
        lines.append(
            "  This audit reports which axes were not varied. Whether an unvaried axis "
            "mattered to this hypothesis is a judgement it does not make."
        )
        return "\n".join(lines)


def audit_exclusion(
    exclusion: Exclusion,
    *,
    fit: Fit | None = None,
    relative_noise: float | None = None,
) -> ExclusionAudit:
    """Attack one exclusion's preconditions. AC 1 and AC 2.

    `fit` is the growth fit the exclusion rests on, where there is one. Supplied
    rather than derived, because an exclusion is a rejected hypothesis and not
    every rejection came from a sweep — an ablation that removed nothing rules
    something out with no exponent anywhere in it, and inventing a fit to judge
    would be auditing a curve nobody drew.
    """
    conditions = exclusion.conditions
    shapes = conditions.observed[Dimension.FIXTURE_SHAPE].values
    concurrency = conditions.observed[Dimension.CONCURRENCY].values
    platforms = conditions.observed[Dimension.PLATFORM].values
    scale_values = conditions.observed[Dimension.SCALE].values

    objections: list[Narrowness] = []

    if len(shapes) == 1:
        # Uniform-only is the sharper objection and gets its own member: `Σ k²`
        # is minimized when every parent is equal, so uniform is provably the
        # blindest shape for any per-parent cost. Long-tail-only is also one
        # shape, and is the deliberate worst case rather than the blindest.
        if shapes[0] == Distribution.UNIFORM.value:
            objections.append(Narrowness.UNIFORM_ONLY)
        else:
            objections.append(Narrowness.SINGLE_SHAPE)

    if all(float(value) <= SERIAL for value in concurrency):
        objections.append(Narrowness.SERIAL_ONLY)

    if len(platforms) == 1:
        objections.append(Narrowness.SINGLE_PLATFORM)

    scales: ScaleAudit | None = None
    if fit is not None:
        numbers = [float(value) for value in scale_values]
        scales = (
            audit_scales(numbers, fit, relative_noise=relative_noise)
            if relative_noise is not None
            else audit_scales(numbers, fit)
        )
        if not scales.adequate:
            objections.append(Narrowness.NARROW_SCALE_SPAN)

    return ExclusionAudit(exclusion=exclusion, objections=tuple(objections), scales=scales)


def audit_all(
    exclusions: Sequence[Exclusion],
    *,
    fits: dict[int, Fit] | None = None,
    relative_noise: float | None = None,
) -> tuple[ExclusionAudit, ...]:
    """Every exclusion, audited. `fits` is keyed by experiment index.

    A missing fit is not an objection: see `audit_exclusion` for why an exclusion
    with no sweep behind it is ordinary rather than suspect.
    """
    supplied = fits or {}
    return tuple(
        audit_exclusion(
            item,
            fit=supplied.get(item.experiment.index),
            relative_noise=relative_noise,
        )
        for item in exclusions
    )


def report(audits: Sequence[ExclusionAudit]) -> str:
    """What the exclusions do and do not establish, for S-9.8 to route on."""
    if not audits:
        return "Nothing was ruled out, so there are no preconditions to attack."
    inadequate = [item for item in audits if not item.adequate]
    lines = [
        f"{len(audits)} exclusion(s) examined; {len(inadequate)} rest on preconditions "
        "narrow enough to be worth widening."
    ]
    lines.extend(item.describe() for item in audits)
    return "\n".join(lines)
