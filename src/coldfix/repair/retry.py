"""Three attempts, and a structural reason to believe the second one differs.

Epic 10, S-10.5. *Three attempts maximum. **Structural check**: attempt 2 is
rejected before running gates if its diff touches the same lines with a similar
edit shape as attempt 1. Failure reasons carried in context. Temperature raised
on retries to force different approaches. Escalates with full attempt history
after three.*

The backlog note is the whole story: ***"must differ in approach" cannot be
self-judged — the agent writes its own approach label and can rename the same
idea.*** `08-audit.md` F12 says the same thing and prescribes the same fix:
*compare the diffs.*

**The check is exact, not fuzzy, and that is deliberate.** The obvious
implementation scores two diffs for similarity and rejects above a threshold —
and the threshold would be a number nobody measured. S-9.4's rule is that a
threshold is **derived or it does not belong**, and there is no measured quantity
here to derive one from: no noise floor, no class gap, nothing. So *similar edit
shape* is defined as an equivalence that can be decided rather than scored — the
same edit, normalized — and the bound is stated rather than hidden.

**Two conditions, and requiring both is what keeps honest retries alive.**
Rejecting on *same lines* alone would refuse the second genuine idea at the same
site: attempt 1 adds a cache at lines 41-52, attempt 2 prefetches at lines 41-52,
and those are different approaches to the same code. Rejecting on *same edit*
alone would refuse the same change applied somewhere else, which is a different
target and therefore a different attempt. A repeat is both at once.

**What it catches and what it does not.** It catches an identical diff, one that
differs only in whitespace, and one that differs only in comments — which is what
*renaming the same idea* looks like when the idea is a diff. It does **not**
catch a renamed local variable, because deciding that two token streams mean the
same thing needs a parser and a judgement, and a judgement is what this check
exists to avoid. Stated here rather than left for somebody to discover.

**`Phase.REPAIR`'s three-attempt cap has had no caller since S-5.4** — the third
of these in Epic 10 after `FINDING_AUDIT` (S-9.8) and `TEST_AUDIT` (S-10.3).
Whoever owns the unit counts the unit, and an attempt is this module's unit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from coldfix.cost.accounting import Phase
from coldfix.cost.budget import Budget, BudgetExhaustedError
from coldfix.repair.patch import SURGEON_TEMPERATURE, Attempt, Patch
from coldfix.sandbox.patching import hunk_lines, hunk_ranges

RETRY_TEMPERATURE = 0.6
"""`03-agents.md` §5.1: *0.2 first attempt, 0.6 on retries.*

Its own justification, quoted because it is the reason this is not one constant:
*higher temperature on retries is deliberate — a retry at 0.2 tends to produce a
variation of the same idea, which will fail the same way.* The structural check
below is what happens when that fails anyway."""

_COMMENT = re.compile(r"\s*#.*$")


class RetryError(Exception):
    """This attempt may not be made, or the attempts are spent."""


class RepeatedAttemptError(RetryError):
    """The attempt repeats an earlier one and is refused before any gate runs.

    Distinct from exhaustion, deliberately, and for S-5.4's reason: they call for
    opposite actions. A repeat means *think again, you still have attempts*; an
    exhausted budget means *stop*. A caller catching one type would handle the
    other wrongly.
    """


@dataclass(frozen=True)
class Repetition:
    """Which earlier attempt this one repeats, and on what evidence."""

    index: int
    """One-based position in the attempt history, as a human would count it."""

    files: tuple[str, ...]
    """Where the two overlap. Named so a reader can look, rather than being told
    that a similarity score crossed a line."""

    def describe(self) -> str:
        return (
            f"attempt {self.index} changed the same lines of {list(self.files)} with the same edit"
        )


def normalized_edit(diff: str) -> frozenset[tuple[str, str]]:
    """The change a diff makes, with the things that are not the change removed.

    Added and removed lines only — context lines are the file, not the edit —
    stripped of surrounding whitespace and of trailing comments, and collected as
    a **set** so that reordering two independent hunks is not a new idea.

    Comments go because a diff that differs only in what it says about itself is
    the textual form of renaming an approach. Whitespace goes because reindenting
    is not a second attempt.
    """
    edit: set[tuple[str, str]] = set()
    for marker, content in hunk_lines(diff):
        if marker == " ":
            continue
        stripped = _COMMENT.sub("", content).strip()
        if stripped:
            edit.add((marker, stripped))
    return frozenset(edit)


def shared_lines(first: str, second: str) -> Mapping[str, frozenset[int]]:
    """Per file, the original-side lines both diffs touch.

    Original-side because two attempts that both rewrite lines 41-52 are working
    on the same code however many lines each added — `hunk_ranges` records why.
    """
    left = hunk_ranges(first)
    right = hunk_ranges(second)
    shared = {path: left[path] & right[path] for path in left.keys() & right.keys()}
    return {path: lines for path, lines in shared.items() if lines}


def repeats(latest: Patch, prior: Sequence[Attempt]) -> Repetition | None:
    """Whether this attempt is an earlier one wearing a different label. AC 2.

    **Both conditions, and neither alone.** The attempts must touch overlapping
    original lines in at least one shared file *and* make the same normalized
    edit. Same lines with a different edit is the second genuine idea at the same
    site; the same edit at different lines is a different target. Only both
    together is the failure F12 describes.

    Returns the repetition rather than a boolean so the refusal can name which
    attempt and which files, which is what makes it checkable by a human rather
    than an assertion they have to take on trust.

    **Two edits that both normalize to nothing are a repeat, and a guard against
    that was deleted.** It was written on the worry that an empty edit compares
    equal to every other empty edit — but a diff whose only change is a comment
    *is* a no-op patch, and a second one at the same lines is the same no-op
    again. The guard's only reachable effect was to call that pair different, and
    S-3.12 recorded the rule: a guard no test reaches is a guard nobody has
    checked.
    """
    edit = normalized_edit(latest.diff)

    for index, attempt in enumerate(prior, start=1):
        if normalized_edit(attempt.patch.diff) != edit:
            continue
        overlap = shared_lines(attempt.patch.diff, latest.diff)
        if overlap:
            return Repetition(index=index, files=tuple(sorted(overlap)))
    return None


def check_attempt(latest: Patch, prior: Sequence[Attempt]) -> None:
    """Refuse a repeat **before any gate runs**. AC 2.

    Nothing in this module executes anything: there is no runner, no session and
    no worktree, so a caller cannot have spent a test run before reaching it. The
    ordering the acceptance criterion asks for is a property of what this
    function needs rather than of where a caller happens to put it.

    Raises:
        RepeatedAttemptError: it changes the same lines with the same edit as an earlier
            attempt, so running the gates would spend a test run to learn what
            the last one already established.
    """
    repetition = repeats(latest, prior)
    if repetition is None:
        return

    message = (
        f"this attempt repeats attempt {repetition.index}: it changes the same lines of "
        f"{list(repetition.files)} with the same edit, differing only in whitespace, "
        f"comments or the approach label. `08-audit.md` F12: *must differ in approach* is "
        f"self-judged, so the diffs are compared instead. Refused before the gates run, "
        f"because they would cost a test run to establish what attempt "
        f"{repetition.index} already did"
    )
    raise RepeatedAttemptError(message)


def temperature_for(attempt: int) -> float:
    """`03-agents.md` §5.1's two temperatures. AC 4.

    Attempt 1 at 0.2, every retry at 0.6. Two values rather than a ramp, because
    §5.1 gives two and inventing a third point on a curve would be a number with
    no argument behind it.

    Raises:
        RetryError: an attempt number below one, which is not a position in any
            history.
    """
    if attempt < 1:
        message = f"attempt numbers are one-based; {attempt} is not a position in a history"
        raise RetryError(message)
    return SURGEON_TEMPERATURE if attempt == 1 else RETRY_TEMPERATURE


def authorize_attempt(budget: Budget, finding_id: str | None = None) -> None:
    """Refuse a fourth attempt before it spends anything. AC 1.

    **The cap is S-5.4's and is not reimplemented here** —
    `Cap(3, ATTEMPT, FINDING, ESCALATE)`, compiled since Epic 5 — but nothing has
    ever counted an attempt, because `Session.run` records a step only where a
    phase's cap counts steps. Third such cap in Epic 10 after `FINDING_AUDIT` and
    `TEST_AUDIT`, and the same remedy: whoever owns the unit counts it.

    Raises:
        BudgetExhaustedError: three attempts are spent. Its disposition is
            `ESCALATE`, which is what AC 5 asks for.
    """
    budget.authorize(Phase.REPAIR, finding_id)


def record_attempt(budget: Budget, attempt: Attempt, finding_id: str | None = None) -> None:
    """Count one completed attempt, with its failure as the stall conclusion.

    The failure rather than the approach, for the reason `Attempt` exists: the
    approach is the agent's own label and S-5.4's stall check is explicitly *not*
    a self-judged criterion. Three attempts failing the same way is a phase
    repeating itself, and the failure is what makes that visible.
    """
    budget.record_step(Phase.REPAIR, finding_id, conclusion=attempt.failure.strip())


@dataclass(frozen=True)
class Escalation:
    """Three attempts, none of which worked, and everything a human needs. AC 5."""

    attempts: tuple[Attempt, ...]
    finding_id: str | None = None

    def report(self) -> str:
        lines = [
            f"REPAIR ESCALATED after {len(self.attempts)} attempt(s)"
            + (f" on {self.finding_id}" if self.finding_id else "")
            + " — no patch survived its own falsification test.",
        ]
        lines.extend(
            f"  {index}. {item.patch.approach}\n"
            f"     touched: {sorted(item.patch.files)}\n"
            f"     failed because: {item.failure}"
            for index, item in enumerate(self.attempts, start=1)
        )
        lines.append(
            "  Every attempt is here, with its diff available on the artifact rather than "
            "summarized away: §7.2's disposition for this phase is *escalate with the "
            "history*, and a history that dropped the attempts would be a count."
        )
        return "\n".join(lines)


def escalate(attempts: Sequence[Attempt], finding_id: str | None = None) -> Escalation:
    """Build the escalation. AC 5.

    Raises:
        RetryError: there are no attempts, so nothing was tried and there is
            nothing to escalate — a caller in that state has a different problem
            and an empty history would hide it.
    """
    if not attempts:
        message = (
            "escalating with no attempts. Nothing was tried, so this is not a repair that "
            "ran out of ideas — it is a repair that never started, and a report saying "
            "otherwise would send somebody looking for three diffs that do not exist"
        )
        raise RetryError(message)
    return Escalation(attempts=tuple(attempts), finding_id=finding_id)


def exhausted(budget: Budget, finding_id: str | None = None) -> bool:
    """Whether the attempts are spent, without spending one to find out.

    A question rather than an exception, because a caller deciding *do I try
    again or escalate* is asking, not acting. `authorize_attempt` is what refuses.
    """
    try:
        budget.authorize(Phase.REPAIR, finding_id)
    except BudgetExhaustedError:
        return True
    return False
