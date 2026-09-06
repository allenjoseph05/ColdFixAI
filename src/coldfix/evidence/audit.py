"""Attacking a finding before any repair money is spent on it.

S-21.1. Four attacks, none of which calls a model. `CLAUDE.md` is explicit --
*do not add a model call where a function would do; counting, curve fitting,
stack grouping and byte comparison are code* -- and an audit that asked a model
whether a payoff cleared the noise floor would be paying for an opinion about a
number it could compute, and getting a less reliable one.

They run **before** the model call in S-21.2, so a finding that fails one costs
nothing to reject. That ordering is the whole economics of the node.

**An attack that cannot fire is not an attack.** Each of these is written so
there is a real input that fails it, and each has a test that supplies one. A
check that passes on everything is a line of code that reads like a safeguard.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel

from coldfix.collect.measurement import BareMeasurement
from coldfix.evidence.ledger import EvidenceError, Finding, Ledger

MINIMUM_MEASUREMENTS = 3
"""A payoff is a comparison, and a comparison drawn from two numbers is a line
through two points -- it fits perfectly and says nothing. Three is the smallest
count at which the third can disagree."""

GUARD_MARGIN = 1.05
"""How much another metric may drift before it counts as having worsened. Not
zero: a peak-memory figure that moved by a kilobyte between two runs moved
because it is a measurement, not because anything changed."""


class Verdict(StrEnum):
    """What the audit decided, in the routes the graph leaves by."""

    SOUND = "sound"
    NEEDS_EVIDENCE = "needs_evidence"
    """Survivable: the claim may be true and what supports it is not enough yet."""

    UNSOUND = "unsound"
    """The evidence contradicts the claim. More of it will not help."""


class Attack(BaseModel, frozen=True):
    """One attempt to break a finding, and whether it survived."""

    name: str
    held: bool
    detail: str
    fatal: bool = False
    """Whether failing this means the claim is wrong, rather than unproven."""


class Audit(BaseModel, frozen=True):
    """Every attack that was tried, and what they add up to."""

    attacks: tuple[Attack, ...]
    verdict: Verdict

    @property
    def survived(self) -> bool:
        return self.verdict is Verdict.SOUND

    def why(self) -> str:
        broken = [attack for attack in self.attacks if not attack.held]
        if not broken:
            return "survived every attack"
        return "; ".join(f"{attack.name}: {attack.detail}" for attack in broken)


def attack(finding: Finding, *, ledger: Ledger) -> Audit:
    """Run all four, then decide. Nothing here is asked of a model."""
    attacks = (
        _still_attested(finding, ledger),
        _enough_measurements(finding, ledger),
        _above_the_noise(finding, ledger),
        _no_guard_worsened(finding, ledger),
    )
    return Audit(attacks=attacks, verdict=_verdict(attacks))


def _verdict(attacks: tuple[Attack, ...]) -> Verdict:
    """Fatal beats survivable, and survivable beats sound.

    The distinction matters to the graph: `needs_evidence` sends the run back to
    look again, and `unsound` does not, because a claim the evidence contradicts
    is not one more experiment away from being true.
    """
    if any(not attack.held and attack.fatal for attack in attacks):
        return Verdict.UNSOUND
    if any(not attack.held for attack in attacks):
        return Verdict.NEEDS_EVIDENCE
    return Verdict.SOUND


def _still_attested(finding: Finding, ledger: Ledger) -> Attack:
    """Re-check every cited number against the ledger as it stands now.

    The ledger checked them when the finding was built. This matters because a
    `Finding` is a Pydantic model that survives a checkpoint, and this pipeline
    rewinds: a finding restored into a run whose measurements are gone, or whose
    ids were reused, would carry numbers nothing supports any more.
    """
    try:
        ledger.attest(finding.claim)
    except EvidenceError as broken:
        return Attack(
            name="still_attested",
            held=False,
            detail=f"the ledger no longer supports this claim: {broken}",
            fatal=True,
        )
    return Attack(name="still_attested", held=True, detail="every cited number still checks out")


def _enough_measurements(finding: Finding, ledger: Ledger) -> Attack:
    """A payoff drawn from too few numbers.

    A suspicion carries no number and needs no comparison, so this only applies
    where one is claimed.
    """
    if finding.claim.payoff is None:
        return Attack(
            name="enough_measurements", held=True, detail="no payoff claimed, so none to support"
        )

    behind = {citation.measurement_id for citation in finding.claim.evidence}
    if finding.claim.proof is not None:
        behind.add(finding.claim.proof.measurement_id)
        # An ablation carries a whole measurement on each side, and those are the
        # two the comparison is actually between.
        behind |= _sides(finding.claim.proof.measurement_id, ledger)

    if len(behind) < MINIMUM_MEASUREMENTS:
        return Attack(
            name="enough_measurements",
            held=False,
            detail=(
                f"{len(behind)} measurement(s) stand behind a payoff of "
                f"{finding.claim.payoff}. A comparison drawn from two numbers is a line "
                "through two points; it fits perfectly and says nothing"
            ),
        )
    return Attack(
        name="enough_measurements", held=True, detail=f"{len(behind)} measurements behind it"
    )


def _above_the_noise(finding: Finding, ledger: Ledger) -> Attack:
    """A payoff smaller than the spread of the run it was measured on.

    The attack most likely to fire, and the one worth the most. A 3% saving on a
    workload that varies by 8% between identical runs has not been observed -- it
    has been guessed at, from inside the noise.
    """
    proof = finding.claim.proof
    if proof is None:
        return Attack(name="above_the_noise", held=True, detail="no measured payoff to check")

    before = _before(proof.measurement_id, ledger)
    if before is None:
        return Attack(
            name="above_the_noise",
            held=True,
            detail="the baseline is not a timed measurement, so there is no noise floor to clear",
        )

    floor = before.wall.relative
    if proof.share_removed <= floor:
        return Attack(
            name="above_the_noise",
            held=False,
            detail=(
                f"the payoff is {proof.share_removed:.1%} and the baseline varies by "
                f"{floor:.1%} between identical runs. A difference inside the spread has not "
                "been observed"
            ),
            fatal=True,
        )
    return Attack(
        name="above_the_noise",
        held=True,
        detail=f"{proof.share_removed:.1%} against a {floor:.1%} noise floor",
    )


def _no_guard_worsened(finding: Finding, ledger: Ledger) -> Attack:
    """Something else got worse while the thing being claimed got better.

    Queries down and rows up is not an improvement; faster and three times the
    memory is a trade to be shown, not a win to be reported.
    """
    proof = finding.claim.proof
    if proof is None:
        return Attack(name="no_guard_worsened", held=True, detail="no ablation to check")

    before = _before(proof.measurement_id, ledger)
    after = _after(proof.measurement_id, ledger)
    if before is None or after is None:
        return Attack(
            name="no_guard_worsened", held=True, detail="the ablation carries no guard metrics"
        )

    worsened = [
        f"{name} {was} -> {now}" for name, was, now in _guards(before, after) if _grew(was, now)
    ]
    if worsened:
        return Attack(
            name="no_guard_worsened",
            held=False,
            detail=(
                f"removing this work made something else worse: {', '.join(worsened)}. "
                "A saving paid for elsewhere is a trade, and it has to be shown as one"
            ),
        )
    return Attack(name="no_guard_worsened", held=True, detail="nothing else moved against us")


def _grew(was: int, now: int) -> bool:
    """Whether a guard moved against us.

    The zero case is separate and it is the one that matters most: a counter that
    read nothing before and reads something now has not drifted, it has started.
    Written as `was > 0 and now > was * MARGIN` this was skipped entirely, and a
    test that removed work and began reading from disk passed.
    """
    if was == 0:
        return now > 0
    return now > was * GUARD_MARGIN


def _guards(before: BareMeasurement, after: BareMeasurement) -> tuple[tuple[str, int, int], ...]:
    """The metrics that must not grow when work is removed."""
    return tuple(
        (name, getattr(before, name) or 0, getattr(after, name) or 0)
        for name in ("peak_rss_bytes", "read_bytes", "write_bytes")
        if getattr(before, name, None) is not None and getattr(after, name, None) is not None
    )


def _side(measurement_id: str, ledger: Ledger, which: str) -> BareMeasurement | None:
    """One half of an ablation, rebuilt from what the ledger stored."""
    record = ledger.recorded(measurement_id)
    if record is None:
        return None
    fields = {
        key[len(which) + 1 :]: value for key, value in record.items() if key.startswith(f"{which}.")
    }
    if not fields:
        return None
    try:
        return BareMeasurement.model_validate(_nest(fields))
    except ValueError:
        return None


def _before(measurement_id: str, ledger: Ledger) -> BareMeasurement | None:
    return _side(measurement_id, ledger, "before")


def _after(measurement_id: str, ledger: Ledger) -> BareMeasurement | None:
    return _side(measurement_id, ledger, "after")


def _sides(measurement_id: str, ledger: Ledger) -> set[str]:
    """The ids of the two runs an ablation compared, if it holds them."""
    found = set()
    for which in ("before", "after"):
        side = _side(measurement_id, ledger, which)
        if side is not None:
            found.add(side.measurement_id)
    return found


def _nest(flat: Mapping[str, object]) -> dict[str, object]:
    """Undo the ledger's flattening for one level of nesting."""
    nested: dict[str, object] = {}
    for key, value in flat.items():
        head, _, rest = key.partition(".")
        if rest:
            branch = nested.setdefault(head, {})
            if isinstance(branch, dict):
                branch[rest] = value
        else:
            nested[key] = value
    return nested
