"""What a person is shown at each gate, and what stops them being shown less.

Epic 12, S-12.4 and S-12.5. `03-agents.md` §1.5: *`interrupt_before` — human
approves on Thursday; state resumes intact.* The parking is LangGraph's and
S-12.2 made it durable; what this module owns is the rest — **what the human
sees** and the refusal that fires when the state cannot show it.

**Two gates, two questions, two reports.** `pending` answers *is this patch right*
after everything is paid for; `found` answers *is this worth trying to fix*
before the Surgeon spends anything, which is `08-audit.md` F16's whole point.
Neither report carries the other's evidence: a `Finding` has nowhere to put a
patch because there is not one yet, and a report with an empty patch section
invites the reader to answer the later question with the earlier one's material.

**There is no trust-level parameter here, and that is the point.** S-12.4 puts the
gate at trust level 0 and S-13.4's third criterion is that *new projects start at
level 0 regardless of cross-project history*, so until that ledger exists level 0
is the only value any project can be at. A parameter would have one reachable
value, and the danger is not that nobody could flip it — it is that somebody
**could**, turning the gate off with no ledger to justify it. `repair/slack.py`
made the same argument for the same reason: *there is no trust-level parameter,
and that is the enforcement.*

**Nothing here is assembled from a summary.** Every field is read off the channel
the phase that produced it wrote, so a report cannot describe a patch that is not
the one parked at the gate. `00-BRIEF.md` §4 requires a slack-reducing patch to
carry its warning *prominently*, and that flag rides on the handover rather than
being recomputed here — recomputing it would be a second classifier that
disagrees with S-10.6 the first time either moves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from coldfix.diagnosis.chain import EvidenceChain
from coldfix.orchestrator.adapters import MissingInputError
from coldfix.orchestrator.checkpointing import thread
from coldfix.repair.patch import Patch
from coldfix.repair.slack import LABEL
from coldfix.report.pullrequest import deltas
from coldfix.state.checkpoint import CheckpointedState
from coldfix.state.trust import Level


class GateError(Exception):
    """The run could not be presented to a human."""


class NotAtTheGateError(GateError):
    """Asked for an approval when the run is not parked at one.

    Separate from an incomplete one, because *this run has not reached ship* and
    *this run reached ship with nothing to show* send a reader to two different
    places — the first is a run still working and the second is a defect.
    """


@dataclass(frozen=True)
class Approval:
    """Everything AC 2 requires, and nothing computed here.

    Frozen, because this is what a person read before deciding. An approval that
    could be edited after the fact is not a record of what was approved.
    """

    finding: str
    chain: EvidenceChain
    patch: Patch
    verdict: str
    """The Adversary's answer, as S-11.7 worded it. A string because E9 and E11
    own the vocabulary and re-typing it here would be a second enumeration."""

    before: Mapping[str, float]
    after: Mapping[str, float]
    slack_reducing: bool

    @property
    def blocked(self) -> bool:
        """Whether this can be approved at all.

        `00-BRIEF.md` §4 and S-10.6: a slack-reducing patch is blocked from
        auto-approval **permanently**, at any trust level. That is not what this
        gate decides — a human may still say yes — but it is what the report has
        to say out loud.
        """
        return self.slack_reducing

    def render(self) -> str:
        """The report, in the order a reader needs it.

        **The warning first when there is one.** §4 says *prominently*, and a
        label under four screens of diff is not prominent.
        """
        lines: list[str] = []
        if self.slack_reducing:
            lines.append(f"!! {LABEL} — this patch reduces slack and needs judgement, not a nod.")
            lines.append("   `00-BRIEF.md` §4: no trust level clears this one.")
            lines.append("")

        lines.extend(
            [
                f"READY TO SHIP — {self.finding}",
                "",
                "ADVERSARY",
                f"  {self.verdict}",
                "",
                "BEFORE AND AFTER",
                *self._deltas(),
                "",
                "PATCH",
                f"  {self.patch.approach}",
                f"  {self.patch.rationale}",
                "",
                self.patch.diff,
                "",
                "EVIDENCE",
                self.chain.render(),
            ]
        )
        return "\n".join(lines)

    def _deltas(self) -> Sequence[str]:
        """Every metric measured on both sides, with the ones only one side has.

        **Rendered through `report.pullrequest.deltas` since S-16.2**, because the
        pull request shows the same numbers and two renderings of one table is how
        a gate report and a pull request come to disagree about what improved. The
        rule the table encodes is unchanged: a metric present before and absent
        after is a measurement nobody took, not an improvement to zero.
        """
        return deltas(self.before, self.after)


@dataclass(frozen=True)
class Finding:
    """What a person is shown **before** any repair budget is spent. S-12.5.

    `08-audit.md` F16: *`interrupt_before=["ship"]` means the human reviews after
    grounding, screening, investigation, repair and audit are all paid for — if
    they would have rejected the direction, the whole budget is gone.* This is the
    same reader, three phases earlier, and the question they are answering is
    narrower: **not is this patch right, but is this worth trying to fix.**

    So it carries no patch and has nowhere to put one. There is not one yet, and a
    report with an empty patch section invites the reader to answer the later
    question with the earlier question's evidence.
    """

    finding: str
    chain: EvidenceChain
    audit: str
    """S-9.8's `Routing.describe()` — the verdict, where it sends the run, and why
    that rather than the obvious. `because` is not decoration: two of the five
    routes are reached from more than one verdict, and a reader who cannot tell
    those apart cannot act on either."""

    subject: str
    spends_repair: bool
    """Whether the route from here reaches the Surgeon. **The premise of the whole
    gate** — a finding going back for more experiments, or stopping, spends no
    repair budget, so there is nothing for a person to approve or decline."""

    def render(self) -> str:
        return "\n".join(
            [
                f"FOUND — {self.finding}",
                f"  audited against: {self.subject}",
                "",
                self.audit,
                "",
                "EVIDENCE",
                self.chain.render(),
                "",
                (
                    "Next is repair, which spends the Surgeon's attempts."
                    if self.spends_repair
                    else "This finding does not reach repair, so nothing is waiting on you."
                ),
            ]
        )


def found(state: CheckpointedState) -> Finding:
    """What the human at the early checkpoint is shown. **S-12.5 AC 2.**

    Raises:
        NotAtTheGateError: no finding audit has been recorded, so the run has not
            reached this checkpoint.
        MissingInputError: an audit was recorded and the chain it audited is not
            in the state, which is a defect rather than a state a run reaches.
    """
    audit = _latest(state, "finding_audit")
    if audit is None:
        message = (
            "no finding audit has been recorded, so this run has not reached the early "
            "checkpoint. Every route to `repair` runs through `audit_finding`"
        )
        raise NotAtTheGateError(message)

    return Finding(
        finding=str(state.target) if state.target is not None else "an unnamed finding",
        chain=EvidenceChain.model_validate(
            _needed(state.chain, "chain", "there is no evidence behind it")
        ),
        audit=str(audit["finding_audit"]),
        subject=str(audit.get("subject", "an unrecorded subject")),
        spends_repair=bool(audit.get("spends_repair", False)),
    )


def waiting_at(graph: Any, run_id: str) -> tuple[str, ...]:  # noqa: ANN401 - see `assemble`
    """Which nodes this run would take next. Empty when it has finished. **AC 1.**

    **Asked of the graph rather than of the checkpointer**, and the difference is
    the whole criterion. `progress_of` reads channel values, which say what a run
    has *written*; parking before `ship` writes nothing, so a state-only view
    cannot tell a run that stopped at the gate from one that ran `ship` and
    happened to change nothing. The pending task is the only place the pause is
    visible.
    """
    return tuple(graph.get_state(thread(run_id)).next)


def pending(state: CheckpointedState) -> Approval:
    """What the human at the gate is shown. **AC 2.**

    Raises:
        NotAtTheGateError: no patch is parked — the run has not reached `ship`.
        MissingInputError: a patch is parked and something it needs is absent,
            which is a defect in an earlier node rather than a state a run
            reaches. **Raised rather than rendered as blanks**, because a person
            shown an approval with an empty evidence section will read it as *no
            evidence* rather than as *the report is broken*, and the first of
            those is a reason to reject a good patch.
    """
    if state.repaired is None:
        message = (
            "no patch is parked at the gate. `repaired` is empty, so either this run has not "
            "reached `ship` or it shipped and cleared the channel — neither is a run awaiting "
            "approval"
        )
        raise NotAtTheGateError(message)

    handover = state.repaired
    if not isinstance(handover, Mapping):  # pragma: no cover - written by `_repaired` only
        message = "the patch handover is not a mapping, so there is no patch to show"
        raise MissingInputError(message)

    audit = _latest_audit(state)
    return Approval(
        finding=str(state.target) if state.target is not None else "an unnamed finding",
        chain=EvidenceChain.model_validate(
            _needed(state.chain, "chain", "there is no evidence to show for it")
        ),
        patch=Patch.model_validate(handover["patch"]),
        verdict=str(_needed(audit.get("verdict"), "flags", "no audit verdict was recorded")),
        before=_numbers(audit.get("before")),
        after=_numbers(audit.get("after")),
        slack_reducing=bool(handover.get("slack_reducing", False)),
    )


def _latest(state: CheckpointedState, kind: str) -> Mapping[str, object] | None:
    """The most recent flag of `kind`, or `None` if there is none.

    **The last one, not the first**, and both gates need that for the same
    reason. S-11.7 sends a broken patch back to the Surgeon and S-9.8 sends an
    unsound finding back for more experiments; either way a second round appends
    another flag, and showing the earliest would present a human with the verdict
    on something already superseded.

    Returns rather than raises, because *no flag of this kind* means one thing at
    the ship gate — a patch parked with nothing that audited it, which is a defect
    — and another at the early one, where it simply means the run has not got
    there. The callers say which.
    """
    for entry in reversed(state.flags):
        if isinstance(entry, Mapping) and kind in entry:
            return entry
    return None


def _latest_audit(state: CheckpointedState) -> Mapping[str, object]:
    """The patch audit for the patch that is parked."""
    found_entry = _latest(state, "patch_audit")
    if found_entry is None:
        message = (
            "a patch is parked at the gate and no patch audit was recorded for it. Every route "
            "to `ship` runs through `audit_patch`, so this state should be unreachable — and "
            "shipping on it would mean shipping a patch the Adversary never saw"
        )
        raise MissingInputError(message)
    return found_entry


def _needed(value: object, channel: str, because: str) -> object:
    if value is None:
        message = f"a run is parked at a gate and {because} ({channel!r} is empty)"
        raise MissingInputError(message)
    return value


def _numbers(value: object) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {str(name): float(str(number)) for name, number in value.items()}


# ============================================================ S-13.6: what the level opens


def gates_for(level: Level) -> Mapping[str, bool]:
    """Which interrupts a project at `level` compiles with. **S-13.6 AC 1.**

    `03-agents.md` §450 gives the rule — *`interrupt_before=["ship"]` at trust
    level 0* — and until S-13.4 there was no ledger, so ADR 130 hardcoded it and
    refused a parameter that could have turned it off with nothing to justify it.
    That refusal was right and is now spent: a level is a thing a project earned,
    recorded append-only, and `standing` is the only way to obtain one.

    **The early checkpoint opens one level before the ship gate**, and the
    asymmetry is ADR 131's: the early one guards a budget and the ship gate guards
    an irreversible outward act, so the cheaper protection is the one to drop
    first.

    | level | early review | ship gate |
    |---|---|---|
    | `GATED` | yes | yes |
    | `FAMILIAR` | no | yes |
    | `TRUSTED` | no | no |

    **`TRUSTED` still does not clear a slack-reducing patch.** That is not decided
    here: the ship node refuses one whatever this returns, because a compile-time
    answer cannot see a patch that does not exist yet.
    """
    return {
        "gated": level < Level.TRUSTED,
        "early_review": level < Level.FAMILIAR,
    }
