"""What the five attacks add up to, and whether the patch goes back or goes on.

Epic 11, S-11.7. *Schema: `clean` / `broken` + reproducing input / `suspicious` +
concern. `broken` requires a reproducing input — schema-enforced. Two rounds
maximum, then escalate.*

**Nothing here calls a model.** Five attacks have already answered; combining them
is `CLAUDE.md`'s *do not add a model call where a function would do* — S-9.8's
first sentence, and it holds harder in this epic, where three of the five attacks
are arithmetic over measurements and never asked a model anything.

**Three verdicts, and no fourth, which is a difference from S-9.8.** That story
needed `inconclusive` as a fifth value because its `unsound` routes back to
*investigate* and spends experiment budget: it had to be able to say *do not spend
that, escalate instead*. Here `suspicious` already means escalate to a human
(`02-architecture.md` §4.4), so an audit that could not see enough is a suspicious
one and needs no new word. **An attack that did not run is a concern, never a
pass** — the same rule, reached without widening the vocabulary the AC names.

**`broken` and `suspicious` differ in whether a demonstration is attached**, and
the routing falls out of that rather than being asserted. §4.4 returns `broken` to
the Surgeon and escalates `suspicious` to a human. A patch that is demonstrably
wrong is something the Surgeon can act on: it gets the reproduction and another
attempt. A patch that is *worrying* is not something the Surgeon can act on — it
has no failing case to fix — so sending it back would produce another guess. The
distinction is not severity. It is whether there is something to run.

That is why `broken` wins precedence over `suspicious` when both apply. It is the
verdict that spends a cheap repair attempt rather than a human, and the concerns
travel in `results` where the human sees them if it comes back.

**A reproduction has two sources and that is why it is a type.** S-11.2 produces
one directly — an adversarial input and the program that shows the difference.
S-11.5's suite produces the other: the suite command, on the patched revision,
failing where it passed before. Both are *a thing somebody can run to see it
again*, which is what §222's *return to Surgeon with reproducing input* is for,
and one of them is not a `ReproducingInput`. `CLAUDE.md` allows the abstraction
once a second case exists; this is the second case.

**Two rounds is S-5.4's cap, and this story is finally its caller.** S-11.1 wired
`Phase.PATCH_AUDIT`'s `authorize_round` and `record_round` and left the round's
*conclusion* to the caller, in as many words, because S-11.2 to S-11.5 had not
defined their verdicts. They have now, so this module is that caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from coldfix.audit.cheating import CheatAudit
from coldfix.audit.equivalence import Equivalence
from coldfix.audit.patchaudit import authorize_round, record_round
from coldfix.audit.scoping import ScopeAudit, SuiteOutcome
from coldfix.audit.trades import TradeAudit
from coldfix.cost.budget import Budget, BudgetExhaustedError
from coldfix.repair.testaudit import TestAudit


class PatchVerdictError(Exception):
    """A patch verdict could not be formed."""


class Attack(StrEnum):
    """The five attacks of `03-agents.md` §6.3, one per story of this epic."""

    EQUIVALENCE = "equivalence: do the two revisions still answer the same"
    CHEAT = "cheat: is the improvement real"
    TRADE = "trade: what got worse beside what got better"
    SCOPE = "scope: who else calls this, and does the suite still pass"
    TEST_QUALITY = "test quality: could a cheat have passed the test that judged this"


class Outcome(StrEnum):
    """What happened to one attack. Four, and `BROKE_IT` is not a louder `SUSPECT`.

    The two landing outcomes differ in whether a demonstration came with them, not
    in how bad the news is. `BROKE_IT` means *here is something you can run*;
    `SUSPECT` means *a person should look at this*. Collapsing them would send
    every concern back to the Surgeon with nothing to fix, or every failure to a
    human who then has to reproduce it themselves.
    """

    PASSED = "ran, and found nothing"
    BROKE_IT = "ran, and produced something that can be run to see it again"
    SUSPECT = "ran, and found something a person has to weigh"
    NOT_RUN = "did not run, or could not see enough to answer"


LANDED = (Outcome.BROKE_IT, Outcome.SUSPECT, Outcome.NOT_RUN)
"""The outcomes that are useless without their text — the objection, the concern,
or the gap. `PASSED` is the only one that says everything by being itself."""


class Reproduction(BaseModel):
    """Something somebody can run to see the problem again. **AC 2's payload.**

    Two sources, which is what makes this a type rather than S-11.2's
    `ReproducingInput` passed straight through: that story produces an input and a
    program, and S-11.5's suite produces a command that fails on one revision and
    not the other. §222 asks for *a reproducing input*; what it needs is the
    ability to re-run, and an objection nobody can re-run is one nobody can act
    on.
    """

    model_config = ConfigDict(frozen=True)

    attack: Attack
    shows: str
    """What running it demonstrates, in one line."""

    how: str
    """The program or command. Long is fine — this is what gets pasted."""

    @model_validator(mode="after")
    def _runnable(self) -> Self:
        if not self.shows.strip() or not self.how.strip():
            message = (
                "a reproduction needs both what it shows and how to run it. Either half alone "
                "is a claim the recipient has to take on trust, which is the thing this epic "
                "exists not to do"
            )
            raise PatchVerdictError(message)
        return self

    def describe(self) -> str:
        lines = [f"  {self.shows}", "  Run this to see it again:"]
        lines.extend(f"    {line}" for line in self.how.splitlines())
        return "\n".join(lines)


@dataclass(frozen=True)
class AttackResult:
    """One attack's answer, in the form the verdict is computed from."""

    attack: Attack
    outcome: Outcome
    detail: str = ""
    reproduction: Reproduction | None = None

    def __post_init__(self) -> None:
        if self.outcome in LANDED and not self.detail.strip():
            message = (
                f"{self.attack.value} — {self.outcome.value} — and said nothing about it. That "
                "text is what a reader acts on; without it the verdict names a problem nobody "
                "can do anything with"
            )
            raise PatchVerdictError(message)
        if (self.reproduction is not None) != (self.outcome is Outcome.BROKE_IT):
            message = (
                f"{self.attack.value} reports {self.outcome.value} and "
                f"{'carries' if self.reproduction is not None else 'carries no'} reproduction. "
                "`BROKE_IT` is defined as the outcome that comes with one, and no other outcome "
                "may carry one — otherwise `broken` could be reached without a demonstration"
            )
            raise PatchVerdictError(message)

    @property
    def landed(self) -> bool:
        return self.outcome in (Outcome.BROKE_IT, Outcome.SUSPECT)

    @property
    def answered(self) -> bool:
        return self.outcome is not Outcome.NOT_RUN

    def describe(self) -> str:
        line = f"{self.attack.value} — {self.outcome.value}"
        return f"{line}\n    {self.detail}" if self.detail.strip() else line


def not_run(attack: Attack, missing: str) -> AttackResult:
    """An attack that did not happen, or could not see enough to answer.

    A constructor rather than a `None` the adapters return, because `None` would
    have to mean both *no answer* and *nothing found* — one value for the two
    things this module exists to keep apart.
    """
    return AttackResult(attack=attack, outcome=Outcome.NOT_RUN, detail=missing)


def from_equivalence(audit: Equivalence) -> AttackResult:
    """S-11.2's answer. The one attack that produces a reproduction directly."""
    if audit.reproducing:
        found = audit.reproducing[0]
        return AttackResult(
            attack=Attack.EQUIVALENCE,
            outcome=Outcome.BROKE_IT,
            detail=(
                f"{len(audit.reproducing)} inputs make the two revisions disagree; "
                f"first: {found.input.shape.value} — {found.summary}"
            ),
            reproduction=Reproduction(
                attack=Attack.EQUIVALENCE,
                shows=f"{found.input.label}: {found.summary}",
                how=found.program,
            ),
        )
    if not audit.survived:
        return not_run(
            Attack.EQUIVALENCE,
            (
                f"{len(audit.inconclusive)} inputs were never driven and "
                f"{len(audit.unstable)} would not settle, so no difference was ruled out"
            ),
        )
    return AttackResult(attack=Attack.EQUIVALENCE, outcome=Outcome.PASSED)


def from_cheat(audit: CheatAudit) -> AttackResult:
    """S-11.3's answer. A cheat is a concern, never a `broken`.

    Nothing here produces a case the Surgeon can run: *the improvement only exists
    warm* is a judgement about a set of measurements, and handing it back as a
    failing input would be handing back an input that does not exist.
    """
    if audit.detected:
        named = ", ".join(check.cheat.name.lower() for check in audit.detected)
        return AttackResult(
            attack=Attack.CHEAT,
            outcome=Outcome.SUSPECT,
            detail=f"the improvement may not be real — {named}: {audit.detected[0].reason}",
        )
    if audit.survives_a_fresh_process is False:
        return AttackResult(
            attack=Attack.CHEAT,
            outcome=Outcome.SUSPECT,
            detail="the improvement is there on repeated passes and gone on a cold one",
        )
    if not audit.complete:
        missing = ", ".join(check.cheat.name.lower() for check in audit.untested)
        return not_run(Attack.CHEAT, f"{len(audit.untested)} classes were never checked: {missing}")
    return AttackResult(attack=Attack.CHEAT, outcome=Outcome.PASSED)


def from_trades(audit: TradeAudit) -> AttackResult:
    """S-11.4's answer. Also always a concern, and for a sharper reason.

    A resource that rose is a fact, not a failure: whether memory tripling is
    acceptable is a question about the deployment, and no test this system can
    write answers it. `08-audit.md` F10's whole point is that these are the trades
    **nobody predicted**, which is precisely the set nobody has agreed a threshold
    for.
    """
    if audit.broken_guards:
        broken = ", ".join(item.guard.metric for item in audit.broken_guards)
        return AttackResult(
            attack=Attack.TRADE,
            outcome=Outcome.SUSPECT,
            detail=f"a declared guard was broken: {broken}",
        )
    if audit.uncovered:
        risen = ", ".join(breach.metric for breach in audit.uncovered)
        return AttackResult(
            attack=Attack.TRADE,
            outcome=Outcome.SUSPECT,
            detail=f"{risen} rose past tolerance and no declared guard was watching",
        )
    if not audit.complete:
        return not_run(
            Attack.TRADE,
            (
                f"{len(audit.unmeasured)} envelope resources were never read and "
                f"{len(audit.unevaluated_guards)} declared guards could not be evaluated"
            ),
        )
    return AttackResult(attack=Attack.TRADE, outcome=Outcome.PASSED)


def from_scope(audit: ScopeAudit, *, suite_command: Sequence[str]) -> AttackResult:
    """S-11.5's answer, and the second source of a reproduction.

    A suite that passed before the change and fails after it is the one thing this
    attack produces that somebody can **run**: the command is the reproduction.
    Callers outside the evidence are a concern — there is no failing case, only a
    list of places nobody looked.
    """
    if audit.suite.broke_it:
        return AttackResult(
            attack=Attack.SCOPE,
            outcome=Outcome.BROKE_IT,
            detail=(
                f"the full suite passed on the original and failed on the patch "
                f"(exit {audit.suite.patched_exit})"
            ),
            reproduction=Reproduction(
                attack=Attack.SCOPE,
                shows="the suite passes before the change and fails after it",
                how=" ".join(suite_command),
            ),
        )
    if audit.outside:
        where = ", ".join(sorted({caller.site.path for caller in audit.outside}))
        return AttackResult(
            attack=Attack.SCOPE,
            outcome=Outcome.SUSPECT,
            detail=f"{len(audit.outside)} call sites outside the evidence: {where}",
        )
    if audit.suite.outcome is not SuiteOutcome.PASSED_ON_BOTH or not audit.complete:
        return not_run(
            Attack.SCOPE,
            f"{audit.suite.outcome.value}; {len(audit.unreadable)} touched files were unreadable",
        )
    return AttackResult(attack=Attack.SCOPE, outcome=Outcome.PASSED)


def from_test_quality(audit: TestAudit) -> AttackResult:
    """S-11.6's answer. A weak test makes the **verification** suspect, not the patch.

    Nothing here says the change is wrong. It says the thing that judged the
    change would not have noticed if it were, which is a different sentence and
    goes to a human rather than back to the Surgeon — the Surgeon has nothing to
    fix, and would only be asked to satisfy a test it has already satisfied.
    """
    if not audit.sound:
        named = ", ".join(item.cheat.name.lower() for item in audit.weaknesses)
        return AttackResult(
            attack=Attack.TEST_QUALITY,
            outcome=Outcome.SUSPECT,
            detail=(
                f"a cheat would have passed the test that judged this patch ({named}); a "
                "strengthened replacement was written and this patch was not judged by it"
            ),
        )
    return AttackResult(attack=Attack.TEST_QUALITY, outcome=Outcome.PASSED)


class Verdict(StrEnum):
    """AC 1's vocabulary, and there are exactly three."""

    CLEAN = "clean: every attack ran and none landed, proceed to ship"
    BROKEN = "broken: something can be run to show the patch is wrong"
    SUSPICIOUS = "suspicious: a person has to weigh this before it ships"


class PatchVerdict(BaseModel):
    """The patch audit's answer, and the artifact the graph routes on.

    A Pydantic model rather than a dataclass because `CLAUDE.md` requires one for
    every artifact crossing a node boundary, and this crosses the last one before
    a human.

    **The validators are AC 2.** *`broken` requires a reproducing input —
    schema-enforced* is a validator and not a convention, so a `broken` with
    nothing to run cannot be constructed at all, by this module or by anything
    downstream that builds one by hand.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    reproduction: Reproduction | None = None
    concern: str = ""
    results: tuple[AttackResult, ...] = ()
    """What each attack answered, kept rather than reduced. A human weighing a
    `suspicious` needs to see that the other four passed, and a Surgeon handed a
    `broken` needs the concerns that travelled with it."""

    @model_validator(mode="after")
    def _broken_can_be_reproduced(self) -> Self:
        """**AC 2.** The one rule this schema exists to carry."""
        if self.verdict is Verdict.BROKEN and self.reproduction is None:
            message = (
                "`broken` with nothing to run. `02-architecture.md` §222 returns this verdict to "
                "the Surgeon *with a reproducing input*, and one that arrives without it asks "
                "the recipient to find the failure themselves — which is how a correct patch "
                "gets rewritten to chase an objection nobody could check"
            )
            raise PatchVerdictError(message)
        if self.verdict is not Verdict.BROKEN and self.reproduction is not None:
            message = (
                f"{self.verdict.value} carries a reproduction. Only `broken` does: a `clean` or "
                "`suspicious` with something runnable attached is an artifact saying two things "
                "at once"
            )
            raise PatchVerdictError(message)
        return self

    @model_validator(mode="after")
    def _suspicious_states_its_concern(self) -> Self:
        has = bool(self.concern.strip())
        if self.verdict is Verdict.SUSPICIOUS and not has:
            message = (
                "`suspicious` with no concern stated. §4.4 escalates this verdict to a human, "
                "and an escalation with no instruction in it is a person asked to review "
                "something nobody would tell them about"
            )
            raise PatchVerdictError(message)
        if self.verdict is Verdict.CLEAN and has:
            message = "`clean` carries a concern, so it is not clean"
            raise PatchVerdictError(message)
        return self

    @property
    def unanswered(self) -> tuple[AttackResult, ...]:
        return tuple(item for item in self.results if not item.answered)

    @property
    def ships(self) -> bool:
        return self.verdict is Verdict.CLEAN

    def describe(self) -> str:
        lines = [f"PATCH AUDIT — {self.verdict.value}"]
        if self.reproduction is not None:
            lines.extend(self.reproduction.describe().splitlines())
        if self.concern.strip():
            lines.append(f"  {self.concern}")
        if self.results:
            lines.append("  Attacks:")
            lines.extend(f"    {item.describe()}" for item in self.results)
        return "\n".join(lines)


def verdict_for(results: Sequence[AttackResult]) -> PatchVerdict:
    """Combine the five attacks into one verdict. No model, and no judgement.

    **Precedence, and the argument for it.** `broken` wins over `suspicious`
    wherever both apply, because §4.4 sends the first back to the Surgeon and the
    second to a human. A patch with a failing case is one the Surgeon can act on,
    and spending a cheap repair attempt is better than spending a person — while
    the concerns travel in `results` and reach that person if it comes back.

    `clean` needs **every** attack to have passed, which makes an attack that did
    not run a concern rather than a pass. Five attacks of which two ran and
    neither objected is not a patch that survived an audit, and this is the fifth
    construction in this epic built to say so.

    Raises:
        PatchVerdictError: no attacks were supplied, or two attacks answered.
    """
    if not results:
        message = (
            "a verdict over no attacks. Nothing landed because nothing was attempted, and the "
            "shape of that answer is indistinguishable from a patch that survived"
        )
        raise PatchVerdictError(message)

    seen = [item.attack for item in results]
    if len(set(seen)) != len(seen):
        repeated = sorted({item.value for item in seen if seen.count(item) > 1})
        message = f"two results for the same attack: {repeated}. Which one counts is undefined"
        raise PatchVerdictError(message)

    broke = [item for item in results if item.outcome is Outcome.BROKE_IT]
    if broke:
        first = broke[0]
        return PatchVerdict(
            verdict=Verdict.BROKEN,
            reproduction=first.reproduction,
            results=tuple(results),
        )

    worrying = [item for item in results if item.outcome in (Outcome.SUSPECT, Outcome.NOT_RUN)]
    if worrying:
        return PatchVerdict(
            verdict=Verdict.SUSPICIOUS,
            concern="; ".join(f"{item.attack.name.lower()}: {item.detail}" for item in worrying),
            results=tuple(results),
        )

    missing = sorted(item.value for item in set(Attack) - set(seen))
    if missing:
        return PatchVerdict(
            verdict=Verdict.SUSPICIOUS,
            concern=f"these attacks were never attempted: {missing}",
            results=tuple(results),
        )
    return PatchVerdict(verdict=Verdict.CLEAN, results=tuple(results))


class Route(StrEnum):
    """Where the patch goes next. §4.4's three consequences."""

    SHIP = "proceed to layer 5"
    RETURN_TO_SURGEON = "back to the Surgeon with the reproducing input"
    ESCALATE = "to a human, with the concern stated"


@dataclass(frozen=True)
class Routing:
    """The verdict, and what happens because of it."""

    route: Route
    verdict: PatchVerdict
    because: str

    def describe(self) -> str:
        return f"{self.route.value} — {self.because}\n{self.verdict.describe()}"


def route(budget: Budget, verdict: PatchVerdict, finding_id: str | None = None) -> Routing:
    """**AC 3.** Where this goes, and what happens when the rounds run out.

    `clean` ships and `suspicious` escalates, neither of which spends a round.
    `broken` returns to the Surgeon **only while a round remains** — that is the
    two-round cap, and past it a patch that keeps coming back broken escalates
    instead of cycling.

    The cap is checked by `authorize_round`, which S-11.1 wired to
    `Phase.PATCH_AUDIT` and left without this caller. A round is *recorded* by the
    caller that ran one; this function only decides whether another may start.
    """
    if verdict.verdict is Verdict.CLEAN:
        return Routing(
            route=Route.SHIP, verdict=verdict, because="every attack ran and none landed"
        )
    if verdict.verdict is Verdict.SUSPICIOUS:
        return Routing(
            route=Route.ESCALATE,
            verdict=verdict,
            because="§4.4 sends a suspicious patch to a human; there is nothing here to re-run",
        )

    try:
        authorize_round(budget, finding_id)
    except BudgetExhaustedError:
        return Routing(
            route=Route.ESCALATE,
            verdict=verdict,
            because=(
                "the patch is broken and both audit rounds are spent. A third attempt would be "
                "the same loop with a bigger bill"
            ),
        )
    return Routing(
        route=Route.RETURN_TO_SURGEON,
        verdict=verdict,
        because="the patch is broken and there is something the Surgeon can run",
    )


def record(budget: Budget, verdict: PatchVerdict, finding_id: str | None = None) -> None:
    """Count one completed round, with the verdict as the stall conclusion.

    **S-11.1 left this to the caller in as many words**, because S-11.2 to S-11.5
    had not defined their verdicts and inventing a vocabulary there would have
    fixed a shape those stories owned. They have defined them, so this module is
    that caller and the conclusion is the verdict's own name.
    """
    record_round(budget, verdict.verdict.name.lower(), finding_id)
