"""What the run found, and — just as loudly — where it did not look.

S-20.2 and S-20.3. A narrow run honestly described is worth more than a broad
claim nobody can support, and the difference between the two is entirely whether
coverage is stated. So the report cannot be built without stating it.

**`not_measured` has no default.** Omitting it is a `ValidationError`, not an
empty list. A default would let a report that never checked the allocator look
identical to one that checked and found nothing, and *"not the allocator"* is
only sayable in the second case. Writing `()` is a claim — *nothing was out of
reach* — and a claim should have to be made rather than fallen into.

**Proven and suspected are never sorted together.** A suspicion carries no
number, so ranking them in one list would mean inventing one or treating a
missing payoff as zero; the first is a fabrication and the second buries real
suspicions under trivial proofs. They are two lists, printed in that order.

**A run that drove nothing reports nothing.** Not an empty finding list with a
confident header — a report that says the program could not be exercised, and
carries no findings at all because it is not entitled to any.
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from coldfix.collect.measurement import Unmeasured
from coldfix.collect.tiers import Capabilities, Tier
from coldfix.evidence.ledger import EvidenceError, Finding


class UnsupportedReportError(EvidenceError):
    """A report claims more than the run is entitled to claim."""


class FindingsWithoutCoverageError(UnsupportedReportError):
    """Nothing was driven, and findings were reported anyway."""

    def __init__(self, count: int) -> None:
        super().__init__(
            f"{count} finding(s) were reported by a run that drove no code path. A finding "
            "describes something observed happening; a run that exercised nothing observed "
            "nothing, and the honest report is that the program could not be measured."
        )


class Coverage(BaseModel, frozen=True):
    """What was exercised, and what was there and was not."""

    driven: tuple[str, ...]
    not_driven: tuple[str, ...] = ()

    @property
    def measured_anything(self) -> bool:
        return bool(self.driven)

    def statement(self) -> str:
        if not self.driven:
            return "no code path was driven; nothing here was exercised"
        line = f"drove {', '.join(self.driven)}"
        if self.not_driven:
            line += f"; did not drive {', '.join(self.not_driven)}"
        return f"{line}. This report covers only what was driven."


class Report(BaseModel, frozen=True):
    """Findings, and the boundaries of the run that produced them."""

    subject: str
    capabilities: Capabilities
    coverage: Coverage
    driver: str
    findings: tuple[Finding, ...]
    not_measured: tuple[Unmeasured, ...]
    """No default, on purpose. See the module docstring."""

    @model_validator(mode="after")
    def _a_run_that_drove_nothing_may_claim_nothing(self) -> Report:
        if not self.coverage.measured_anything and self.findings:
            raise FindingsWithoutCoverageError(len(self.findings))
        return self

    @property
    def proven(self) -> tuple[Finding, ...]:
        """Ablation behind every one, ordered by what removing the work is worth."""
        return tuple(
            sorted(
                (f for f in self.findings if f.proven),
                key=lambda f: f.claim.payoff or 0.0,
                reverse=True,
            )
        )

    @property
    def suspected(self) -> tuple[Finding, ...]:
        """No number, so no ordering claim. Kept in the order they were found."""
        return tuple(f for f in self.findings if not f.proven)

    def headline(self) -> str:
        if not self.coverage.measured_anything:
            return f"{self.subject}: could not be measured"
        return (
            f"{self.subject}: {len(self.proven)} proven, "
            f"{len(self.suspected)} suspected  ·  {self.capabilities.statement()}"
        )

    def render(self) -> str:
        """The whole thing as text, with the limits before the results."""
        lines = [
            self.headline(),
            self.coverage.statement(),
            "",
        ]
        if self.proven:
            lines.append("PROVEN — the work was removed and the cost went with it")
            for finding in self.proven:
                claim = finding.claim
                payoff = f"{(claim.payoff or 0) * 100:5.1f}%"
                lines.append(
                    f"  {payoff}  {claim.location.file}:{claim.location.line}"
                    f"  {claim.location.symbol or claim.kind}"
                )
                lines.append(f"          {claim.summary}")
                lines.append(f"          attested against {', '.join(finding.attested_against)}")
        if self.suspected:
            lines.extend(["", "SUSPECTED — worth chasing, and carrying no number"])
            for finding in self.suspected:
                claim = finding.claim
                lines.append(
                    f"     ---  {claim.location.file}:{claim.location.line}"
                    f"  {claim.location.symbol or claim.kind}"
                )
                lines.append(f"          {claim.summary}")
        if not self.findings:
            lines.append("NOTHING FOUND — a run that proves nothing wasteful is a result")

        lines.extend(["", "NOT MEASURED"])
        gaps = list(self.not_measured)
        gaps.extend(
            Unmeasured(what=name, why=f"unavailable at tier {int(self.capabilities.tier)}")
            for name in self.capabilities.unavailable
        )
        if gaps:
            lines.extend(f"  {gap.what}: {gap.why}" for gap in gaps)
        else:
            lines.append("  nothing — every dimension this toolkit measures was available")

        lines.extend(["", "DRIVER — the script every number above came from", ""])
        lines.extend(f"  {line}" for line in self.driver.splitlines())
        return "\n".join(lines)


def unmeasurable(subject: str, capabilities: Capabilities, why: str) -> Report:
    """The report for a run that could not exercise the program.

    A complete outcome with its own shape, rather than an ordinary report whose
    findings list happens to be empty — those two look the same on the page and
    mean opposite things.
    """
    return Report(
        subject=subject,
        capabilities=capabilities,
        coverage=Coverage(driven=()),
        driver="",
        findings=(),
        not_measured=(Unmeasured(what="everything", why=why),),
    )


__all__ = [
    "Coverage",
    "FindingsWithoutCoverageError",
    "Report",
    "Tier",
    "UnsupportedReportError",
    "unmeasurable",
]
