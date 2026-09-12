"""What a person reads at the ship gate. **S-29.3, ADR 188.**

`ship` parks the run and says so; this is what the person parked in front of
actually sees. Nothing here measures, derives, or decides -- every number comes
from a measurement the harness took and every verdict from an attack that ran.
A report that computed a figure would be the one place a finding could gain a
number no experiment supports.

**Proven and suspected are `Basis`, not a second opinion.** `Claim.basis` is
already the partition -- `ABLATION` is the only basis that may be called proven,
and everything else is *worth chasing, worth nothing as a number*. Classifying
again here would be a second answer to one question, and the two would disagree
the first time either moved. Both halves are always printed, including when one
is empty, because a report that silently omits the suspected half reads as though
everything in it was proven.

**The prominence slot goes to an unproven payoff.** v1's gate put a
slack-reducing warning first, for `00-BRIEF.md` §4's word *prominently*: a label
under four screens of diff is not prominent. The v3 equivalent is shipping a
patch for a finding whose payoff was never ablation-proven -- the patch may be
perfectly good and the reason for wanting it is a suspicion, and that belongs
above the diff rather than below it.

**The audit is re-validated, not trusted.** `audited` is a JSON dump in a
checkpoint, so `PatchReview`'s computed properties are gone and its verdict is
just a string sitting beside its evidence. Validating it back re-runs the check
that refuses a verdict the comparisons contradict -- so a `clean` written over a
break cannot reach a reader through this module, however it got into the channel.

**Absence renders as absence.** A section with nothing behind it says that, and
a handover missing something it needs raises rather than rendering blanks: a
person shown an empty evidence section reads *no evidence* rather than *the
report is broken*, and the first of those is a reason to reject a good patch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from coldfix.evidence.adversary import PatchReview
from coldfix.evidence.ledger import Basis, Finding
from coldfix.pipeline.state import PipelineState

UNPROVEN_WARNING = (
    "THE PAYOFF FOR THIS FINDING WAS NEVER ABLATION-PROVEN. The patch below was "
    "measured and audited; the reason for wanting it is a suspicion."
)


class GateError(Exception):
    """The run could not be presented to a person."""


class NotAtTheGateError(GateError):
    """Asked for the gate report when no patch is parked.

    Kept distinct from a handover that is present and unreadable, because *this
    run has not reached ship* and *this run reached ship with nothing to show*
    send a reader to two different places: the first is a run still working, the
    second is a defect.
    """


class UnreadableHandoverError(GateError):
    """A patch is parked and something it needs is absent or self-contradictory.

    Raised rather than rendered as blanks. This is a defect in an earlier node,
    not a state a run reaches, and a reader shown gaps will read them as findings
    about the patch.
    """


@dataclass(frozen=True)
class Reviewed:
    """One of the run's findings, as the gate presents it."""

    identifier: str
    kind: str
    summary: str
    where: str
    basis: Basis
    attested_against: tuple[str, ...]
    verdict: str

    @property
    def proven(self) -> bool:
        return self.basis is Basis.ABLATION

    def describe(self) -> str:
        cited = ", ".join(self.attested_against) or "nothing"
        return (
            f"  {self.identifier} {self.kind} at {self.where}: {self.summary}\n"
            f"    verdict {self.verdict or 'not audited'}; attested against {cited}"
        )


@dataclass(frozen=True)
class GateReport:
    """Everything the person deciding needs, and nothing computed here."""

    finding: Reviewed
    approach: str
    share_removed: float
    measurement_id: str
    test: str
    diff: str
    trades: tuple[str, ...]
    review: PatchReview
    proven: tuple[Reviewed, ...]
    suspected: tuple[Reviewed, ...]
    coverage: Mapping[str, str]

    def lines(self) -> list[str]:
        """The report, in the order a reader needs it."""
        return [
            *self._warning(),
            f"READY TO SHIP — {self.finding.identifier}: {self.approach}",
            f"  {self.share_removed:.1%} faster, measured as {self.measurement_id}",
            "",
            "ADVERSARY",
            *self._adversary(),
            "",
            "PROVEN — the payoff was ablated, and the cost went with it",
            *self._listed(self.proven),
            "",
            "SUSPECTED — worth chasing, worth nothing as a number",
            *self._listed(self.suspected),
            "",
            "COVERAGE — how far each phase got",
            *self._coverage(),
            "",
            *self._trades(),
            "THE TEST — it failed on the unpatched revision, which is what makes it one",
            "",
            self.test,
            "",
            "THE DIFF",
            "",
            self.diff,
        ]

    def _warning(self) -> Sequence[str]:
        if self.finding.proven:
            return []
        return [f"!! {UNPROVEN_WARNING}", ""]

    def _adversary(self) -> Sequence[str]:
        lines = [
            f"  {self.review.verdict.value} after {self.review.turns} turn(s), "
            f"{len(self.review.comparisons)} input(s) run on both revisions"
        ]
        if not self.review.comparisons:
            lines.append("  nothing was compared. Surviving an unmounted attack is not surviving")
        lines.extend(
            f"  broke on {list(item.given)}: {item.why()}" for item in self.review.reproducing
        )
        lines.extend(f"  {note}" for note in self.review.notes)
        return lines

    def _listed(self, findings: Sequence[Reviewed]) -> Sequence[str]:
        return [item.describe() for item in findings] or ["  none"]

    def _coverage(self) -> Sequence[str]:
        if not self.coverage:
            return ["  nothing was recorded, so nothing here says what was not looked at"]
        return [f"  {phase}: {outcome}" for phase, outcome in sorted(self.coverage.items())]

    def _trades(self) -> Sequence[str]:
        if not self.trades:
            return []
        return [
            "TRADE-OFFS — faster, and paid for somewhere else",
            *(f"  {approach}" for approach in self.trades),
            "",
        ]


def awaiting_review(state: PipelineState) -> bool:
    """Whether a patch is parked for a person.

    Here rather than at the call site, so *what counts as being at the gate* has
    one owner. Two spellings of it is how a caller comes to render a report the
    node would have refused.
    """
    return state.repaired is not None


def report_for(state: PipelineState) -> GateReport:
    """Assemble the gate report. Renders nothing, decides nothing, measures nothing.

    Raises:
        NotAtTheGateError: no patch is parked, so either the run has not reached
            `ship` or it shipped and cleared the channel. Neither is a run
            awaiting approval.
        UnreadableHandoverError: a patch is parked and the handover, the audit or
            a finding cannot be read -- including an audit whose verdict its own
            comparisons contradict.
    """
    if not awaiting_review(state):
        message = (
            "no patch is parked at the gate. `repaired` is empty, so either this run has not "
            "reached `ship` or it shipped and cleared the channel -- neither is a run awaiting "
            "approval"
        )
        raise NotAtTheGateError(message)

    handover = state.repaired
    if not isinstance(handover, Mapping):
        message = f"the patch handover is {type(handover).__name__}, not a mapping"
        raise UnreadableHandoverError(message)

    identifier = str(_needed(handover, "finding"))
    reviewed = {name: _reviewed(name, payload, state) for name, payload in state.findings.items()}
    shipping = reviewed.get(identifier)
    if shipping is None:
        message = (
            f"the patch at the gate names finding {identifier!r}, which is not in `findings`. "
            "The report would describe a patch for something the run never recorded"
        )
        raise UnreadableHandoverError(message)

    return GateReport(
        finding=shipping,
        approach=str(_needed(handover, "approach")),
        share_removed=float(str(_needed(handover, "share_removed"))),
        measurement_id=str(_needed(handover, "measurement_id")),
        test=str(_needed(handover, "test")),
        diff=str(_needed(handover, "diff")),
        trades=tuple(str(item) for item in _sequence(handover.get("trades"))),
        review=_review(state),
        proven=tuple(item for item in reviewed.values() if item.proven),
        suspected=tuple(item for item in reviewed.values() if not item.proven),
        coverage={str(phase): str(outcome) for phase, outcome in state.coverage.items()},
    )


def _review(state: PipelineState) -> PatchReview:
    """The patch audit, validated back into the model that computed it."""
    if state.audited is None:
        message = (
            "a patch is parked and no patch audit was recorded. Every route to `ship` runs "
            "through `audit_patch`, so this is a defect rather than a run that skipped it"
        )
        raise UnreadableHandoverError(message)
    try:
        return PatchReview.model_validate(state.audited)
    except ValidationError as unreadable:
        message = (
            "the recorded patch audit is not one this system produced: "
            f"{unreadable.errors()[0]['msg']}. The verdict is re-checked against its own "
            "comparisons here, so a verdict they contradict cannot reach a reader through "
            "this report"
        )
        raise UnreadableHandoverError(message) from unreadable


def _reviewed(identifier: str, payload: object, state: PipelineState) -> Reviewed:
    try:
        finding = Finding.model_validate(payload)
    except ValidationError as unreadable:
        message = (
            f"finding {identifier!r} is not one this system attested: "
            f"{unreadable.errors()[0]['msg']}"
        )
        raise UnreadableHandoverError(message) from unreadable

    claim = finding.claim
    resolution = state.resolved.get(identifier)
    verdict = resolution.get("verdict") if isinstance(resolution, Mapping) else None
    return Reviewed(
        identifier=identifier,
        kind=claim.kind,
        summary=claim.summary,
        where=f"{claim.location.file}:{claim.location.line}",
        basis=claim.basis,
        attested_against=finding.attested_against,
        verdict=str(verdict) if verdict is not None else "",
    )


def _needed(handover: Mapping[str, object], key: str) -> object:
    value = handover.get(key)
    if value is None:
        message = (
            f"a patch is parked at the gate and its {key!r} is missing. Rendering the section "
            "empty would read as a fact about the patch rather than as a broken report"
        )
        raise UnreadableHandoverError(message)
    return value


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, (list, tuple)) else ()
