"""The pull request body, and the two things in it a reviewer cannot check for themselves.

Epic 16, S-16.2. `adapters.ship` has said since S-12.7 that it does F14 *and
nothing else* — *the pull request is S-16.2, two epics away, and a stub here
would be a second, worse answer to a question another epic owns.* This is that
answer, and it is also the last thing standing between the pipeline and S-17.1's
finding branch.

**Most of the body already existed and is not rebuilt.** `EvidenceChain.render`
is S-16.1 and produces the symptom, the mechanism, the localization, the growth
table, the site, the implicated files and every exclusion *with its
preconditions*; `Approval.render` established the before-and-after table and the
rule that a metric measured on one side only says so. `deltas` moved here so
that table has one owner rather than two spellings, and the gate now renders
through it.

**The guards are read off the falsification test, never passed in.** AC 1 asks
for *guard metrics showing what did not regress*, and the only honest source is
the test that declared them: `CostClaim.guards` is what S-10.1 required
non-empty, because *a cost claim with no guard is a test a cheat passes by moving
one number*. A pull request taking its own guard list could show a reviewer
guards the test never checked — a report of a check nobody ran, which is the
shape `Outcome.NOT_RUN` exists to keep distinct everywhere else in this system.

**A guard nobody measured after the patch is unverified, not satisfied.** Same
rule as the delta table and the same reason: absence renders as absence. Reading
a missing measurement as *within its limit* would put the most flattering
available number under a reviewer's signature, and the guard-counter
non-negotiable exists precisely to stop that.

**Round one's reproductions travel with round two's patch.** S-11.7 sends a
broken patch back with a reproducing input and the Surgeon tries again; the patch
that ships is the survivor. A reviewer who can see what the earlier attempt got
caught by is better placed than one who sees only the version that passed, and
the `Reproduction` type already exists because *an objection nobody can re-run is
one nobody can act on*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from coldfix.audit.patchverdict import Attack, PatchVerdict, Reproduction, Verdict
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.repair.falsification import Guard
from coldfix.repair.mustfail import Falsified
from coldfix.repair.patch import Patch
from coldfix.repair.slack import LABEL, REVIEWED_AT_EVERY_LEVEL

REGRESSION_TEST_PATH = "tests/regression"
"""Where the falsification test is proposed as a permanent one. **AC 2.**

A directory rather than a filename, because the finding's id is what makes the
file unique and this module does not get to decide a project's test layout. The
body says where it goes; a human or S-16.2's caller puts it there."""


class ReportError(Exception):
    """A pull request could not be assembled from what was supplied."""


def deltas(before: Mapping[str, float], after: Mapping[str, float]) -> Sequence[str]:
    """Every metric measured on both sides, and the ones only one side has.

    **Moved here from `gate.Approval` at S-16.2 so the table has one owner.** Two
    renderings of the same numbers is how a gate report and a pull request come to
    disagree about what improved.

    A metric present before and absent after is **not** an improvement to zero. It
    is a measurement nobody took, and rendering it as a delta would invent the
    most flattering number available.
    """
    names = sorted(set(before) | set(after))
    rows: list[str] = []
    for name in names:
        was, now = before.get(name), after.get(name)
        if was is None or now is None:
            rows.append(f"  {name}: measured on only one side, so no delta")
            continue
        rows.append(f"  {name}: {was:g} -> {now:g} ({change(was, now)})")
    return rows or ["  nothing was measured on either side"]


def change(was: float, now: float) -> str:
    """How much better or worse, said the way a reader checks it.

    **This is `gate._change`, moved rather than rewritten.** The first draft of
    this module wrote a second one that rendered `-87%` — correct, and worse: a
    signed percentage leaves the reader to work out whether down is good for this
    metric, and half the metrics in a report are guards where it is not.

    A percentage against a zero baseline is undefined rather than infinite, and
    saying so is more useful than printing a number nobody can verify.
    """
    if was == 0:
        return "was zero, so no ratio"
    share = (was - now) / was
    return f"{abs(share):.0%} {'better' if share > 0 else 'worse'}" if share else "unchanged"


@dataclass(frozen=True)
class GuardReading:
    """One guard the falsification test declared, against what was measured.

    Three states rather than two, and the third is the one a report gets wrong:
    a guard whose metric was not measured after the patch has **not** been
    checked, and saying so is different from saying it held.
    """

    guard: Guard
    measured: float | None

    @property
    def checked(self) -> bool:
        return self.measured is not None

    @property
    def held(self) -> bool:
        """Whether the guard was checked **and** the measurement is within it."""
        return self.measured is not None and self.measured <= self.guard.at_most

    def describe(self) -> str:
        if self.measured is None:
            return (
                f"  {self.guard.metric}: NOT MEASURED after the patch — "
                f"{self.guard.describe()}. Unverified, not satisfied."
            )
        verdict = "held" if self.held else "**REGRESSED**"
        return (
            f"  {self.guard.metric}: {self.guard.baseline:g} -> {self.measured:g} "
            f"(limit {self.guard.at_most:g}) — {verdict}"
        )


@dataclass(frozen=True)
class PullRequest:
    """Everything AC 1 lists, assembled from what the run already produced.

    Nothing here is computed about the patch. Every number comes from a
    measurement the harness took, every verdict from an attack that ran, and the
    guards from the test that declared them — this module renders, and a report
    that derived a figure would be the one place a finding could gain a number no
    experiment supports.
    """

    finding: str
    chain: EvidenceChain
    patch: Patch
    falsified: Falsified
    verdict: PatchVerdict
    before: Mapping[str, float]
    after: Mapping[str, float]
    slack_reducing: bool = False
    earlier_rounds: tuple[Reproduction, ...] = field(default_factory=tuple)
    """What a previous round's patch was caught by, if there was one. Empty is the
    ordinary case — most patches pass first time — and is rendered as nothing
    rather than as a heading with nothing under it."""

    @property
    def guards(self) -> tuple[GuardReading, ...]:
        """**AC 1's guard metrics, read off the test that declared them.**"""
        return tuple(
            GuardReading(guard=guard, measured=self.after.get(guard.metric))
            for guard in self.falsified.test.cost.guards
        )

    @property
    def unverified_guards(self) -> tuple[GuardReading, ...]:
        return tuple(item for item in self.guards if not item.checked)

    @property
    def regressed_guards(self) -> tuple[GuardReading, ...]:
        return tuple(item for item in self.guards if item.checked and not item.held)

    def title(self) -> str:
        prefix = f"[{LABEL}] " if self.slack_reducing else ""
        return f"{prefix}{self.finding}: {self.patch.approach}"

    def body(self) -> str:
        """The body, in the order a reviewer needs it.

        **The warning first when there is one**, which is `00-BRIEF.md` §4's word
        *prominently* and `Approval.render`'s rule — a label under four screens of
        diff is not prominent.
        """
        lines: list[str] = []
        if self.slack_reducing:
            lines.extend(
                [
                    f"> **{LABEL.upper()}** — this patch reduces slack and needs judgement,",
                    f"> not a nod. {REVIEWED_AT_EVERY_LEVEL}",
                    "",
                ]
            )

        lines.extend(
            [
                f"## {self.patch.approach}",
                "",
                self.patch.rationale,
                "",
                "## Adversary",
                f"  {self.verdict.verdict.value}",
                *(f"  {item.describe()}" for item in self.verdict.results),
                "",
                "## Before and after",
                *deltas(self.before, self.after),
                "",
                "## Guards — what was not allowed to get worse",
                *self._guard_lines(),
                "",
                "## Test results",
                *self._suite_lines(),
                "",
                *self._earlier_round_lines(),
                "## Regression test",
                *self._regression_lines(),
                "",
                "## Evidence",
                "",
                self.chain.render(),
            ]
        )
        return "\n".join(lines)

    def _guard_lines(self) -> Sequence[str]:
        lines = [item.describe() for item in self.guards]
        if self.regressed_guards:
            lines.append(
                "  **A guard regressed.** `CLAUDE.md`: queries down while rows explode is not "
                "an improvement."
            )
        if self.unverified_guards:
            names = ", ".join(item.guard.metric for item in self.unverified_guards)
            lines.append(
                f"  **{names} was not measured after the patch.** That is unverified rather "
                "than satisfied, and reading it as satisfied would be the most flattering "
                "available answer."
            )
        return lines

    def _suite_lines(self) -> Sequence[str]:
        """The suite attack's answer, or the fact that it did not run.

        Read off the verdict rather than taken as a parameter: S-11.5 runs the
        suite as an attack, and a pull request quoting a separately-supplied
        result could report a green suite the audit never saw.
        """
        found = [item for item in self.verdict.results if item.attack is Attack.SCOPE]
        if not found:
            return ["  the suite was not run as part of this audit"]
        return [f"  {item.describe()}" for item in found]

    def _earlier_round_lines(self) -> Sequence[str]:
        if not self.earlier_rounds:
            return []
        return [
            "## What an earlier round got caught by",
            "",
            "  This patch is not the first attempt. A previous one was returned with a",
            "  reproduction, and it is here because a reviewer who can see what went wrong",
            "  before is better placed than one who sees only the version that passed.",
            "",
            *(
                line
                for item in self.earlier_rounds
                for line in (f"  {item.attack.value}: {item.shows}", f"    {item.how}", "")
            ),
        ]

    def _regression_lines(self) -> Sequence[str]:
        """**AC 2.** The falsification test, as a file to keep.

        With the evidence it failed on the unpatched revision, because that is
        what makes it a regression test rather than a test: S-10.1's whole
        construction is that a test which passes before the patch proves nothing,
        and `Falsified` refuses to describe a failure as a success.
        """
        return [
            f"  Add as `{REGRESSION_TEST_PATH}/test_{_slug(self.finding)}.py` —"
            " this is what the fix is for.",
            "",
            f"  It failed on the unpatched revision: {self.falsified.evidence}",
            f"  ({self.falsified.wall_seconds:g}s)",
            "",
            "```python",
            self.falsified.test.script,
            "```",
        ]


def _slug(finding: str) -> str:
    """A finding id as a filename fragment, with nothing a path separator could use."""
    kept = [character if character.isalnum() else "_" for character in finding.lower()]
    return "".join(kept).strip("_") or "finding"


def pull_request(  # noqa: PLR0913 - the finding, the chain, the patch, the proof
    # it failed unpatched, the audit's verdict and the two measurement sets are
    # seven things six different stories produced. None is derivable from another,
    # and bundling them would invent a type whose only purpose is to be unpacked.
    *,
    finding: str,
    chain: EvidenceChain,
    patch: Patch,
    falsified: Falsified,
    verdict: PatchVerdict,
    before: Mapping[str, float],
    after: Mapping[str, float],
    slack_reducing: bool = False,
    earlier_rounds: Sequence[Reproduction] = (),
) -> PullRequest:
    """Assemble one pull request. Renders; measures nothing; decides nothing.

    Raises:
        ReportError: the audit did not clear this patch. A pull request is what
            a *shipped* patch gets, and building one from a `broken` verdict
            would put the Adversary's own objection at the top of a document
            asking somebody to merge it.
    """
    if verdict.verdict is not Verdict.CLEAN:
        message = (
            f"the patch audit returned {verdict.verdict.value}, so there is no pull request to "
            "write. S-11.7 routes anything else back to the Surgeon or to a human, and a body "
            "assembled here would be asking for a merge the audit declined to recommend"
        )
        raise ReportError(message)

    return PullRequest(
        finding=finding,
        chain=chain,
        patch=patch,
        falsified=falsified,
        verdict=verdict,
        before=before,
        after=after,
        slack_reducing=slack_reducing,
        earlier_rounds=tuple(earlier_rounds),
    )
