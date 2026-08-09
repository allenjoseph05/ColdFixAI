"""Caps that cannot be raised, and four different things "exhausted" means.

Epic 5, S-5.4. `CLAUDE.md`'s hard-enforcement table lists this story against
*budget cannot be exceeded*, and the backlog note is unusually blunt about how:
**caps must be in code, not configuration — the worst case without them is
unbounded** (`04-cost.md` §12.1 puts it at ~€125,000).

Three things about the acceptance criteria turned out to decide the design.

**AC 3 describes the global ceiling, not every cap.** Read as universal —
*exhaustion halts, checkpoints and reports* — it makes three of the four
per-phase caps wrong, because `02-architecture.md` §7.2 gives each phase its own
disposition and only one of them is a halt:

| Phase | Cap | On exhaustion |
|---|---|---|
| Ground | 60 steps | abort with a diagnostic |
| Investigate | 40 experiments | **emit the partial chain with its exclusions** |
| Repair | 3 attempts | escalate with the history |
| Audit | 2 rounds | escalate |
| Global | euro ceiling | halt, checkpoint, report |

Running out of investigate budget is not a failure and must not throw away what
was measured. *Forty experiments, here is what they showed and here is what this
run therefore does not cover* is an answer — the same answer S-4.5 ships when a
screen finds nothing — and a halt would discard it. So exhaustion carries a
`Disposition`, and the halt is the global ceiling's alone.

**The four caps are in four different units, and conflating them is a 3x error.**
Ground counts steps, investigate counts *experiments*, repair counts attempts,
audit counts rounds. §12.1 budgets 120 model calls per finding in investigate
against a cap of 40 experiments — so an experiment is about three calls, and a
cap counted in calls would halt investigation at a third of its intended budget.
This is S-4.4's finding again (*the unit is a workload, not a flag*), and it is
why `Cap` carries its unit rather than assuming one.

**The caps are scoped differently too.** Grounding happens once per repository
(§11), so its 60 steps are per run. Investigate and repair are per *finding* —
§12.1's table is written per finding — so a single run-wide counter would give
five findings eight experiments each instead of forty.

**A ceiling checked after the call is not a ceiling.** Cost is known only once a
call has returned, so authorization takes the caller's worst case *before*
spending: prompt tokens at the dearest input rate there is — a one-hour cache
write, 2x — plus `max_tokens` of output. Conservative in the only safe direction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from coldfix.cost.accounting import (
    PER_MILLION,
    AccountingError,
    ExchangeRate,
    Ledger,
    Phase,
    price_of,
)

# The dearest an input token can be: a one-hour cache write, at 2x the input
# rate. Worst-case authorization assumes every prompt token is one, because the
# alternative is a ceiling that holds only when the caching went well.
_DEAREST_INPUT_MULTIPLIER = Decimal("2.0")

# How many consecutive steps may repeat what is already known before the phase
# is called stalled. Three rather than two: two identical results is a
# confirmation, which is a thing an investigation legitimately does.
DEFAULT_STALL_AFTER = 3

# Two steps concluding the same thing is a confirmation, not a stall, so a run of
# repeats shorter than this cannot mean anything.
MINIMUM_STALL_RUN = 2


class BudgetError(AccountingError):
    """A budget could not be applied, or was asked to do something unsafe."""


class CapRaisedError(BudgetError):
    """Something tried to raise a cap above the figure compiled into this module.

    The backlog note is explicit that caps live in code rather than
    configuration, and this is what makes that true of a running process as well
    as of the source file. Lowering is permitted — a caller that wants to spend
    less is not the failure mode anyone is guarding against.
    """


class Disposition(StrEnum):
    """What running out means for the phase that ran out. §7.2's column, verbatim.

    Four values because the table has four, and folding them into one halt would
    lose the only one that produces output.
    """

    ABORT = "abort with a diagnostic"
    PARTIAL = "emit the partial chain, with the exclusions it implies"
    ESCALATE = "escalate to a human, with the history"
    HALT = "halt, checkpoint and report"


class StepUnit(StrEnum):
    """What a cap counts. Never model calls — see the module docstring."""

    STEP = "steps"
    EXPERIMENT = "experiments"
    ATTEMPT = "attempts"
    ROUND = "rounds"


class Scope(StrEnum):
    """What a cap is counted against."""

    RUN = "run"
    """Once for the whole run. Grounding, which §11 shares across findings."""

    FINDING = "finding"
    """Once per finding. §12.1's table is written per finding."""


@dataclass(frozen=True)
class Cap:
    """One phase's limit, its unit, its scope, and what exhausting it means."""

    limit: int
    unit: StepUnit
    scope: Scope
    on_exhaustion: Disposition

    def __post_init__(self) -> None:
        if self.limit <= 0:
            message = (
                f"a cap of {self.limit} {self.unit.value} would let the phase do nothing at all; "
                "a phase that should not run is not configured, it is not called"
            )
            raise BudgetError(message)

    def describe(self) -> str:
        return f"{self.limit} {self.unit.value} per {self.scope.value}"


# `02-architecture.md` §7.2 and S-5.4's acceptance criteria, in code because the
# backlog note requires it. Nothing reads these from a file, an environment
# variable or a constructor argument; `Budget.tighten` may lower one and nothing
# can raise one.
#
# **Each audit phase gets its own two rounds** rather than sharing them. The
# three audits ask different questions of different artifacts — E9 audits a
# finding, the test audit audits a falsification test, E11 audits a patch — and a
# shared pool would let a patch audit spend the budget a finding audit had not
# used yet.
PHASE_CAPS: Mapping[Phase, Cap] = {
    Phase.GROUND: Cap(60, StepUnit.STEP, Scope.RUN, Disposition.ABORT),
    Phase.INVESTIGATE: Cap(40, StepUnit.EXPERIMENT, Scope.FINDING, Disposition.PARTIAL),
    Phase.REPAIR: Cap(3, StepUnit.ATTEMPT, Scope.FINDING, Disposition.ESCALATE),
    Phase.FINDING_AUDIT: Cap(2, StepUnit.ROUND, Scope.FINDING, Disposition.ESCALATE),
    Phase.TEST_AUDIT: Cap(2, StepUnit.ROUND, Scope.FINDING, Disposition.ESCALATE),
    Phase.PATCH_AUDIT: Cap(2, StepUnit.ROUND, Scope.FINDING, Disposition.ESCALATE),
}


@dataclass(frozen=True)
class Exhaustion:
    """A budget that ran out, and everything needed to act on it.

    Carried by the exception rather than returned, following S-1.7's recorded
    argument for `NoiseFloorTooHighError`: refusing by return value lets a caller
    ignore the refusal, and refusing without the evidence makes it unloggable.

    This is also the checkpoint AC 3 asks for. It is a complete, serializable
    statement of where the run stopped and what it had spent — **not** a
    checkpoint schema, which is S-6.1's artifact and is deliberately not guessed
    at here (S-1.7's precedent again).
    """

    phase: Phase | None
    """`None` for the global ceiling, which belongs to no single phase."""

    finding_id: str | None
    disposition: Disposition
    used: int
    limit: int
    unit: StepUnit
    spent_eur: Decimal
    ceiling_eur: Decimal | None

    def report(self) -> str:
        where = "the run" if self.phase is None else f"{self.phase.value}"
        against = f" on {self.finding_id}" if self.finding_id is not None else ""
        ceiling = "no ceiling" if self.ceiling_eur is None else f"€{self.ceiling_eur:.2f}"
        return (
            f"{where}{against} is out of budget: {self.used} of {self.limit} "
            f"{self.unit.value} used, €{self.spent_eur:.2f} spent against {ceiling}. "
            f"What happens next: {self.disposition.value}."
        )


class BudgetExhaustedError(BudgetError):
    """A cap or the ceiling was reached. Carries the exhaustion, not just a message."""

    def __init__(self, exhaustion: Exhaustion) -> None:
        self.exhaustion = exhaustion
        super().__init__(exhaustion.report())


@dataclass(frozen=True)
class Stall:
    """A phase that has stopped learning anything. §7.2's progress check.

    **What counts as new information is decided by the harness, not the agent.**
    `08-audit.md` F6's whole finding was that a self-judged success criterion is
    one the agent is incentivised to claim, and *did that step teach me
    something* is exactly that question. So a step is recorded with a digest of
    its **conclusion** — a growth class, a flag set, a verdict — computed from
    the artifact, and a repeat is a repeat whatever the agent believes.

    The digest is over the conclusion rather than the measurement for S-5.2's
    reason: every measurement carries durations, no duration repeats, and a
    digest over raw numbers would therefore never detect a stall at all.
    """

    phase: Phase
    finding_id: str | None
    repeated: int
    conclusion: str

    def report(self) -> str:
        against = f" on {self.finding_id}" if self.finding_id is not None else ""
        return (
            f"{self.phase.value}{against} has produced the same conclusion {self.repeated} times "
            f"running ({self.conclusion}). Escalate rather than continue: more steps of the same "
            "kind will spend budget without changing the answer."
        )


class ProgressStalledError(BudgetError):
    """N steps produced no new information. Distinct from exhaustion, deliberately.

    They call for opposite actions — an exhausted budget means stop, a stalled
    one means *change approach, you still have budget* — so a caller that caught
    one type would handle the other wrongly.
    """

    def __init__(self, stall: Stall) -> None:
        self.stall = stall
        super().__init__(stall.report())


def worst_case_usd(model: str, prompt_tokens: int, max_output_tokens: int) -> Decimal:
    """The most a call could possibly cost, before it is made.

    Every prompt token is priced as a one-hour cache write — 2x the input rate,
    the dearest an input token can be — and the whole of `max_tokens` is assumed
    to come back. Both are deliberately pessimistic: a ceiling enforced against
    an optimistic estimate holds only when the caching went well, which is the
    run where the ceiling matters least.

    Raises:
        UnknownModelError: the model is not in S-5.3's price book.
        BudgetError: a negative token count.
    """
    if prompt_tokens < 0 or max_output_tokens < 0:
        message = f"token counts cannot be negative: {prompt_tokens}, {max_output_tokens}"
        raise BudgetError(message)

    price = price_of(model)
    return (
        prompt_tokens * price.input_usd * _DEAREST_INPUT_MULTIPLIER
        + max_output_tokens * price.output_usd
    ) / PER_MILLION


@dataclass
class Budget:
    """Per-phase step caps and a global euro ceiling, over S-5.3's ledger.

    The ledger is the source of every euro figure here — a budget that counted
    its own spend could disagree with the bill, and the bill is the one somebody
    checks against an invoice.
    """

    ledger: Ledger
    rate: ExchangeRate
    ceiling_eur: Decimal | None = None
    """`None` means no global ceiling, which is a legitimate development setting
    and never a production one. The per-phase caps still apply — they are not
    optional and there is no way to switch them off."""

    stall_after: int = DEFAULT_STALL_AFTER
    caps: Mapping[Phase, Cap] = field(default_factory=lambda: dict(PHASE_CAPS))
    _used: dict[tuple[Phase, str | None], int] = field(default_factory=dict, repr=False)
    _conclusions: dict[tuple[Phase, str | None], list[str]] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        raised = sorted(
            phase.value
            for phase, cap in self.caps.items()
            if phase in PHASE_CAPS and cap.limit > PHASE_CAPS[phase].limit
        )
        if raised:
            message = (
                f"these caps are above the figures compiled into this module: {raised}. Caps live "
                "in code rather than configuration because the worst case without them is "
                "unbounded (`04-cost.md` §12.1 puts it near €125,000). Lowering one is fine"
            )
            raise CapRaisedError(message)
        if self.stall_after < MINIMUM_STALL_RUN:
            message = (
                f"a stall needs at least two steps to be a repeat, got {self.stall_after}. At one, "
                "the first step of every phase would escalate"
            )
            raise BudgetError(message)
        if self.ceiling_eur is not None and self.ceiling_eur <= 0:
            message = f"a euro ceiling must be positive, got {self.ceiling_eur}"
            raise BudgetError(message)

    def tighten(self, phase: Phase, limit: int) -> None:
        """Lower one phase's cap. Raising it is refused.

        Asymmetric on purpose: a run that wants to spend less than the compiled
        cap is not the failure mode this module exists for, and forbidding it
        would make the caps unusable on a cheap smoke test.

        Raises:
            CapRaisedError: the new limit is above the compiled one.
        """
        compiled = PHASE_CAPS[phase]
        if limit > compiled.limit:
            message = (
                f"cannot raise {phase.value} to {limit}; the cap compiled into this module is "
                f"{compiled.limit} {compiled.unit.value} and nothing at runtime may exceed it"
            )
            raise CapRaisedError(message)
        lowered = Cap(limit, compiled.unit, compiled.scope, compiled.on_exhaustion)
        self.caps = {**self.caps, phase: lowered}

    def _key(self, phase: Phase, finding_id: str | None) -> tuple[Phase, str | None]:
        """Which counter a step lands in — per run, or per finding.

        Grounding is shared across a repository, so its steps are counted once
        for the run however many findings follow. Everything else is per finding,
        because §12.1's table is written per finding and a run-wide counter would
        give five findings a fifth of the budget each.
        """
        cap = self.caps[phase]
        return (phase, None if cap.scope is Scope.RUN else finding_id)

    def used(self, phase: Phase, finding_id: str | None = None) -> int:
        return self._used.get(self._key(phase, finding_id), 0)

    def remaining(self, phase: Phase, finding_id: str | None = None) -> int:
        return self.caps[phase].limit - self.used(phase, finding_id)

    @property
    def spent_eur(self) -> Decimal:
        return self.rate.convert(self.ledger.total_usd)

    def authorize(
        self,
        phase: Phase,
        finding_id: str | None = None,
        worst_case: Decimal = Decimal(0),
    ) -> None:
        """Refuse the next step if it would breach a cap or the ceiling.

        Called **before** the work, which is the only place a ceiling can be
        enforced: cost is known once a call returns, so a check afterwards
        reports a breach rather than preventing one. `worst_case` is what the
        step could cost at its most expensive — `worst_case_usd` computes it.

        Raises:
            BudgetExhaustedError: carrying the exhaustion, its disposition, and
                the spend at the moment it stopped.
        """
        cap = self.caps[phase]
        used = self.used(phase, finding_id)
        if used >= cap.limit:
            raise BudgetExhaustedError(
                Exhaustion(
                    phase=phase,
                    finding_id=finding_id if cap.scope is Scope.FINDING else None,
                    disposition=cap.on_exhaustion,
                    used=used,
                    limit=cap.limit,
                    unit=cap.unit,
                    spent_eur=self.spent_eur,
                    ceiling_eur=self.ceiling_eur,
                )
            )

        if self.ceiling_eur is None:
            return

        projected = self.spent_eur + self.rate.convert(worst_case)
        if projected > self.ceiling_eur:
            raise BudgetExhaustedError(
                Exhaustion(
                    phase=None,
                    finding_id=None,
                    disposition=Disposition.HALT,
                    used=used,
                    limit=cap.limit,
                    unit=cap.unit,
                    spent_eur=self.spent_eur,
                    ceiling_eur=self.ceiling_eur,
                )
            )

    def record_step(
        self, phase: Phase, finding_id: str | None = None, conclusion: str | None = None
    ) -> None:
        """Count a completed step, and check whether the phase is still learning.

        `conclusion` is the harness's digest of what the step established — a
        growth class, a flag set, a verdict. `None` means the step produced no
        conclusion to compare, which resets the run of repeats rather than
        extending it: a step that concluded nothing is not the same conclusion
        twice.

        Raises:
            ProgressStalledError: the last `stall_after` steps concluded the same
                thing, so more of them will spend budget without moving.
        """
        key = self._key(phase, finding_id)
        self._used[key] = self._used.get(key, 0) + 1

        seen = self._conclusions.setdefault(key, [])
        if conclusion is None:
            seen.clear()
            return
        seen.append(conclusion)

        recent = seen[-self.stall_after :]
        if len(recent) == self.stall_after and len(set(recent)) == 1:
            raise ProgressStalledError(
                Stall(
                    phase=phase,
                    finding_id=finding_id if self.caps[phase].scope is Scope.FINDING else None,
                    repeated=self.stall_after,
                    conclusion=conclusion,
                )
            )

    def report(self) -> str:
        """Where every counter stands, for a run report or a checkpoint."""
        ceiling = "none" if self.ceiling_eur is None else f"€{self.ceiling_eur:.2f}"
        lines = [f"Budget: €{self.spent_eur:.2f} spent against a ceiling of {ceiling}"]
        for phase in sorted(self.caps, key=lambda item: item.value):
            cap = self.caps[phase]
            for key, used in sorted(
                ((key, used) for key, used in self._used.items() if key[0] is phase),
                key=lambda item: item[0][1] or "",
            ):
                against = f" ({key[1]})" if key[1] is not None else ""
                lines.append(f"  {phase.value}{against}: {used}/{cap.describe()}")
        return "\n".join(lines)


def dispositions() -> Sequence[tuple[Phase, Disposition]]:
    """Every phase and what running out of it means, for a caller writing a handler.

    Exists so that the four dispositions are enumerable rather than something a
    reader has to notice: a handler written against a single *halt* would be
    wrong for three of the six phases, and silently — it would discard a partial
    investigation that had a real answer in it.
    """
    return [(phase, cap.on_exhaustion) for phase, cap in PHASE_CAPS.items()]
