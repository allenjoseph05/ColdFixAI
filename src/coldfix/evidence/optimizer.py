"""The Optimizer: the prompt behind `repair.Propose`. **S-27.1, ADR 180.**

E22 built the search -- generate several, run every one, keep what measurably
won -- and left `Propose` a callable with nothing behind it. This is what is
behind it.

**One call per round, several candidates per call, and no tools.** The model is
shown the proven finding, the source of the file it names, the test that already
failed, and what earlier rounds measured. It writes diffs. It runs nothing and
reads nothing: applying and measuring are the harness's `Apply`, and the winner is
whatever `Archive` says measured best.

**A candidate has no reason attached, anywhere.** The reply is an approach and a
diff. `Candidate` has no field for why, so the Adversary cannot be shown one --
isolation by absence rather than by remembering to strip a field.

**Refused before it is measured**, each with its reason shown to the next round:
a candidate that touches no file, a file other than the finding's, or a path
`PatchPolicy` protects; one that repeats a measured approach's name, or its edit
under a new name.

**The cascade is by rounds, and its check is the measurement.** A round runs on
the routed tier until `CHEAP_ATTEMPTS` rounds there have produced no candidate
whose tests the harness saw pass; then one rung dearer. The check is read from
the archive the harness wrote, never from the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from coldfix.cost.accounting import Agent, Phase
from coldfix.cost.cascade import CHEAP_ATTEMPTS, NoDearerTierError
from coldfix.cost.routing import StepType
from coldfix.evidence.ledger import Finding
from coldfix.evidence.repair import MAX_CANDIDATES, Archive, Candidate, Falsified, Outcome
from coldfix.llm.client import NON_STREAMING_MAX_TOKENS
from coldfix.llm.metered import Call, Meter
from coldfix.sandbox.patching import (
    DEFAULT_PATCH_POLICY,
    PatchPolicy,
    UnparsablePatchError,
    hunk_lines,
    touched_paths,
)

TEMPERATURE = 0.0
"""Recorded intent (ADR 178): variety comes from asking for approaches that differ
from each other and from the archive, not from sampling -- which the mid and
frontier tiers no longer honour."""

MAX_ROUNDS = 3
"""`15-full-architecture.md`: 8 candidates, 3 rounds. With `CHEAP_ATTEMPTS` at two,
three rounds are exactly §12.3's *mid → frontier*: two on the routed tier, and a
third one rung dearer if neither produced a candidate that passed."""

PER_ROUND = 3
"""Eight candidates over three rounds, rounded up."""

PROPOSE = Call(
    step=StepType.PATCH,
    phase=Phase.REPAIR,
    agent=Agent.OPTIMIZER,
    max_tokens=NON_STREAMING_MAX_TOKENS,
)
"""What a round spends on. `PATCH` is mechanical -- §3's check is *the test suite
passes* -- so it routes below the frontier and may escalate."""

SYSTEM = """\
You write changes that remove a measured cost from a program. Somebody else
proved the cost is there with an experiment, and a test that checks for it has
already failed against the code as it stands. Your change has to make that test
pass without changing what the program outputs.

You do not run anything. Every candidate you write is applied and measured by the
harness -- wall time, peak memory, the test suite, and whether the output is byte
for byte the same -- and the fastest one that breaks nothing wins. A candidate
that reads well and measures slower loses. So write several genuinely different
approaches rather than one careful one: the search finds the fix, not the first
guess.

Change only the file the finding names. A change to any other file, or to a test,
fixture or harness file, is refused before it is measured.

Reply with one JSON object and nothing else:

  {"candidates": [
    {"approach": "prefetch the books with their authors",
     "diff": "--- a/app/models.py\\n+++ b/app/models.py\\n@@ -110,3 +110,3 @@\\n ..."}
  ]}

`approach` names the technique in a few words. Two candidates with different
names and the same edit are one candidate, and only the first is measured.
`diff` is a unified diff against the source exactly as shown.
"""

_OUTCOMES = {
    Outcome.WON: "won",
    Outcome.TRADE: "faster, but paid for in memory",
    Outcome.SLOWER: "measured no faster",
    Outcome.BROKE_OUTPUT: "changed the program's output",
    Outcome.BROKE_TESTS: "broke the test suite",
}


class _Proposed(BaseModel, frozen=True):
    approach: str
    diff: str


class _Reply(BaseModel, frozen=True):
    """What a round's reply must be. Any other key -- a rationale above all -- is
    dropped here, because nothing downstream has a field to carry it."""

    candidates: list[_Proposed]


@dataclass(frozen=True)
class Round:
    """One call: the rung it ran on, and what it handed the search to measure."""

    escalation: int
    proposed: tuple[str, ...]


@dataclass(frozen=True)
class Refusal:
    """A candidate -- or a whole round -- that never reached the harness, and why."""

    round: int
    approach: str
    reason: str


@dataclass
class Optimizer:
    """A `Propose` for one finding. Each call to it is a round.

    Built with a `Falsified`, which only the failing-test gate mints: there is no
    way to ask for a candidate before a test has failed against the code.
    """

    meter: Meter
    finding: Finding
    source: str
    falsified: Falsified
    policy: PatchPolicy = DEFAULT_PATCH_POLICY
    rounds: list[Round] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)

    def __call__(self, archive: Archive) -> tuple[Candidate, ...]:
        """The next round's measurable candidates, or nothing when the rounds are spent.

        A round that yields nothing measurable -- declined, cut off, unreadable,
        or every candidate refused -- is spent, and the next one is asked at once:
        returning nothing would end the search while rounds remained.

        Raises:
            BudgetExhaustedError: the meter refused the round. Nothing was sent,
                and the caller decides what a halt means.
        """
        while len(self.rounds) < MAX_ROUNDS:
            wanted = min(PER_ROUND, MAX_CANDIDATES - len(archive.scored))
            if wanted <= 0:
                return ()
            escalation = self._escalation(archive)
            candidates = self._round(archive, wanted, escalation)
            self.rounds.append(
                Round(escalation=escalation, proposed=tuple(c.identifier for c in candidates))
            )
            if candidates:
                return candidates
        return ()

    def _escalation(self, archive: Archive) -> int:
        """One rung dearer once `CHEAP_ATTEMPTS` rounds on this rung passed nothing.

        "Passed" is `Scored.tests_pass`, which the harness recorded. A round that
        handed the search nothing counts as one that passed nothing.
        """
        rung = self.rounds[-1].escalation if self.rounds else 0
        here = [r for r in self.rounds if r.escalation == rung]
        if len(here) < CHEAP_ATTEMPTS:
            return rung
        passed = {e.candidate.identifier for e in archive.scored if e.tests_pass}
        if any(passed.intersection(r.proposed) for r in here):
            return rung
        try:
            self.meter.model_for(PROPOSE, escalation=rung + 1)
        except NoDearerTierError:
            # Already on the dearest tier: the remaining rounds run there. The
            # round limit bounds the spend, which is what escalation cannot.
            return rung
        return rung + 1

    def _round(self, archive: Archive, wanted: int, escalation: int) -> tuple[Candidate, ...]:
        number = len(self.rounds) + 1
        response = self.meter.complete(
            PROPOSE,
            system=SYSTEM,
            messages=[{"role": "user", "content": self.question(archive, wanted)}],
            temperature=TEMPERATURE,
            escalation=escalation,
        )
        if response.refused:
            return self._spent(number, "the model declined to answer")
        if response.truncated:
            return self._spent(
                number, f"the reply was cut off at {PROPOSE.max_tokens} tokens and was not read"
            )
        try:
            proposed = _Reply.model_validate_json(_unfenced(response.text)).candidates
        except ValidationError as unreadable:
            return self._spent(
                number,
                f"the reply could not be read as candidates: {unreadable.errors()[0]['msg']}",
            )
        if not proposed:
            return self._spent(number, "the reply offered no candidates")

        edits = {_edit(e.candidate.diff) for e in archive.scored}
        kept: list[Candidate] = []
        for index, item in enumerate(proposed[:wanted], start=1):
            objection = self._objection(item, archive, edits)
            if objection is not None:
                self.refusals.append(Refusal(number, item.approach, objection))
                continue
            edits.add(_edit(item.diff))
            kept.append(
                Candidate(
                    identifier=f"r{number}c{index}", approach=item.approach.strip(), diff=item.diff
                )
            )
        return tuple(kept)

    def _spent(self, number: int, reason: str) -> tuple[Candidate, ...]:
        self.refusals.append(Refusal(number, "the whole round", reason))
        return ()

    def _objection(  # noqa: PLR0911 - one return per refusal, each with its own reason
        self, item: _Proposed, archive: Archive, edits: set[frozenset[tuple[str, str]]]
    ) -> str | None:
        """Why this candidate is not worth measuring, or `None`."""
        if not item.approach.strip() or not item.diff.strip():
            return "it is blank"
        if archive.already_tried(item.approach.strip()):
            return "an approach of that name was already measured"
        try:
            touched = touched_paths(item.diff)
        except UnparsablePatchError as unreadable:
            return str(unreadable)
        if not touched:
            return "the diff touches no file"
        for path in sorted(touched):
            rule = self.policy.matching_rule(path)
            if rule is not None:
                return f"{path} decides whether a change worked (protected by {rule!r})"
        site = self.finding.claim.location.file
        outside = sorted(touched - {site})
        if outside:
            return (
                f"it changes {outside}, and the finding names only {site}. A change the evidence "
                "does not reach is one no measurement here supports"
            )
        edit = _edit(item.diff)
        if not edit:
            return "the diff adds and removes no line"
        if edit in edits:
            return "it makes the same edit as a candidate already measured or proposed"
        return None

    def question(self, archive: Archive, wanted: int) -> str:
        """What a round is asked: the brief, what came before, and how many to write."""
        return "\n\n".join(
            part
            for part in (
                render_brief(self.finding, self.source, self.falsified),
                self._history(archive),
                f"Write up to {wanted} candidates, each a different approach.",
            )
            if part
        )

    def _history(self, archive: Archive) -> str:
        """Every measured candidate with its numbers, and every refusal with its reason.

        Outcomes and figures, not labels alone: F12 is that a label is the one part
        a model can change while changing nothing else, so what makes the next
        round different is knowing what each attempt *did*.
        """
        lines: list[str] = []
        if archive.scored:
            base = archive.baseline
            lines.append(f"ALREADY MEASURED -- the code as it stands runs in {base.wall_s:.3f}s")
            for entry in archive.scored:
                outcome = entry.outcome(baseline=base)
                memory = (
                    f", peak {entry.peak_rss_bytes} bytes against {base.peak_rss_bytes}"
                    if outcome is Outcome.TRADE
                    else ""
                )
                lines.append(
                    f"  {entry.candidate.approach}: {_OUTCOMES[outcome]} "
                    f"({entry.wall_s:.3f}s{memory})"
                )
        if self.refusals:
            lines.append("REFUSED BEFORE MEASURING")
            lines.extend(f"  {r.approach}: {r.reason}" for r in self.refusals)
        return "\n".join(lines)


def render_brief(finding: Finding, source: str, falsified: Falsified) -> str:
    """The part of every round's question that does not change between rounds."""
    claim = finding.claim
    where = f"{claim.location.file}:{claim.location.line} {claim.location.symbol}".strip()
    measured = [f"  {c.measurement_id}.{c.field} = {c.value}" for c in claim.evidence]
    proven = (
        f"removing the cause removed {claim.payoff:.1%} of it (ablation "
        f"{claim.proof.measurement_id})"
        if claim.payoff is not None and claim.proof is not None
        else "suspected; no ablation has proved the payoff"
    )
    return "\n".join(
        [
            "THE FINDING",
            f"  where: {where}",
            f"  kind: {claim.kind}",
            f"  what: {claim.summary}",
            f"  payoff: {proven}",
            "  measured:",
            *measured,
            "",
            f"THE SOURCE OF {claim.location.file}",
            source,
            "",
            "THE TEST YOUR CHANGE MUST MAKE PASS -- it already failed on the code above",
            falsified.test,
            f"  it failed with: {falsified.detail}",
        ]
    )


def _edit(diff: str) -> frozenset[tuple[str, str]]:
    """The change a diff makes: added and removed lines, stripped, as a set.

    A set, so reordering two hunks is not a new idea; stripped, so reindenting is
    not either. v1's version also dropped `#` comments, which is one language's
    syntax -- knowledge the v3 core does not carry.
    """
    return frozenset(
        (marker, content.strip())
        for marker, content in hunk_lines(diff)
        if marker != " " and content.strip()
    )


def _unfenced(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
