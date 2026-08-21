"""What a person is shown before a patch ships, and what stops them being shown less.

Epic 12, S-12.4. `03-agents.md` §1.5: *`interrupt_before` — human approves on
Thursday; state resumes intact.* The parking is LangGraph's and S-12.2 made it
durable; what this module owns is the other two criteria — **what the human sees**
and the refusal that fires when the state cannot show it.

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
from coldfix.state.checkpoint import CheckpointedState


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

        A metric present before and absent after is not an improvement to zero —
        it is a measurement that was not taken, and rendering it as a delta would
        invent the most flattering number available.
        """
        names = sorted(set(self.before) | set(self.after))
        rows: list[str] = []
        for name in names:
            was, now = self.before.get(name), self.after.get(name)
            if was is None or now is None:
                rows.append(f"  {name}: measured on only one side, so no delta")
                continue
            rows.append(f"  {name}: {was:g} -> {now:g} ({_change(was, now)})")
        return rows or ["  nothing was measured on either side"]


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


def _latest_audit(state: CheckpointedState) -> Mapping[str, object]:
    """The most recent patch audit in the append-only flag channel.

    **The last one, not the first.** S-11.7 sends a broken patch back to the
    Surgeon and the second round appends another flag; showing the earliest would
    present a human with the verdict on a patch that was already replaced.
    """
    for entry in reversed(state.flags):
        if isinstance(entry, Mapping) and "patch_audit" in entry:
            return entry
    message = (
        "a patch is parked at the gate and no patch audit was recorded for it. Every route to "
        "`ship` runs through `audit_patch`, so this state should be unreachable — and shipping "
        "on it would mean shipping a patch the Adversary never saw"
    )
    raise MissingInputError(message)


def _needed(value: object, channel: str, because: str) -> object:
    if value is None:
        message = f"a patch is parked at the gate and {because} ({channel!r} is empty)"
        raise MissingInputError(message)
    return value


def _numbers(value: object) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {str(name): float(str(number)) for name, number in value.items()}


def _change(was: float, now: float) -> str:
    """How much better or worse, said the way a reader checks it.

    A percentage against a zero baseline is undefined rather than infinite, and
    saying so is more useful than printing a number nobody can verify.
    """
    if was == 0:
        return "was zero, so no ratio"
    share = (was - now) / was
    return f"{abs(share):.0%} {'better' if share > 0 else 'worse'}" if share else "unchanged"
