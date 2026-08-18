"""Detecting the class of change this system is built to produce.

Epic 10, S-10.6 — **SAFETY**. `00-BRIEF.md` §4 states the problem without
softening it:

> **Our tool produces exactly these optimizations.** A caching fix reduces
> steady-state queries, passes every check, and can move a system from stable to
> vulnerable — where the next traffic spike does not recover.

**The gate §4 specifies cannot run, and `08-audit.md` F1 says so.** Metastable
failure needs a sustaining feedback loop — many clients, retry logic, load
balancing, queues feeding each other — and *in a single container with one
synthetic driver, the loop does not exist. We can generate load. We cannot
generate metastability.* So the spike-and-recovery test is **not a precondition
here and is not run at all**; F1 replaces it with static detection plus permanent
manual review, and §7's revised build order records the substitution. Primitive 3
is downgraded from *verification we perform* to **risk class we detect and hand
off**.

`01-primitives.md` §4 still states the old gate — *must pass a spike-and-recovery
test before it may be proposed* — and §15 of the same file records the
downgrade. Where they disagree the audit wins.

**A false negative is the dangerous direction, so this classifier leans toward
flagging.** An unflagged slack-reducing patch reaches auto-approval; a wrongly
flagged one costs somebody a review. That asymmetry is the **opposite** of
S-9.7's, where a wrong `unrepresentative` silently discarded a real finding and
the safe answer was therefore the default. Same reasoning, opposite conclusion,
because what a mistake costs is what decides.

**Two kinds of pattern, and conflating them inverts half of them.** Four are
keywords on *added* lines — a cache appearing, a retry appearing. Two are
**comparisons**: a pool size or a timeout is slack-reducing only when it goes
*down*. An implementation that greps for `timeout` flags the patch that raises
one, which adds headroom, and would teach every reader to ignore the label.

**Added lines only, and the parsing is `patching.py`'s.** A `+` at the start of a
line is content inside a hunk and a file header outside one, and S-2.4 already
solved that by tracking the counts each `@@` declares. Re-deriving it here would
be a second answer to a question with one right answer, in a safety module.

**Nothing here claims the patch was tested for metastability.** AC 6, and F1's
fourth instruction: *do not claim we tested it.* Where S-3.16's
retry-amplification check ran, its result is attached — `01-primitives.md` §15
calls that a *partial rescue* that *catches the common case and no more* — and a
run that found no amplification is reported in that primitive's own words, which
already say it is not proof of safety.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.primitives.faults import Amplification
from coldfix.sandbox.patching import hunk_lines

LABEL = "slack-reducing"
"""The label F1 requires. A constant because Epic 14's ledger has to match on it
and a second spelling would be a trust level cleared by a typo."""

RESIDUE = (
    "This is static detection, not verification. `08-audit.md` F1: a "
    "spike-and-recovery test is not executable in a single container with one "
    "synthetic driver, because metastability needs a sustaining feedback loop that "
    "does not exist there. Nothing here has tested this patch for metastability, "
    "and a patch this classifier does not flag has not been shown to be safe — it "
    "has been shown not to match six patterns."
)

STAGING_WARNING = (
    "Before production, verify recovery after a load spike to 2x capacity. "
    "This system cannot run that test."
)
"""F1's third instruction, near enough verbatim. The second sentence is added
because the first alone reads as a suggestion, and the point is that nobody
downstream should assume the check happened somewhere else."""


class Slack(StrEnum):
    """The six patterns F1 lists, in F1's order.

    A transcription rather than a judgement, the way S-10.1's `Cheat` is. Adding
    a seventh is a line here; deciding one of these is not really slack-reducing
    is a change to the audit.
    """

    CACHE = "a cache or memoization was added"
    RETRY = "retry logic was added"
    CONNECTION_REUSE = "connection reuse was added"
    POOL_SHRUNK = "a pool size was reduced"
    TIMEOUT_SHRUNK = "a timeout was reduced"
    BUFFERING = "buffering was added"

    @property
    def headroom(self) -> str:
        """What margin this removes, for the warning F1 asks to be specific."""
        return _HEADROOM[self]


_HEADROOM: dict[Slack, str] = {
    Slack.CACHE: (
        "the work that used to happen on every request now happens on some of them, so a "
        "cold cache under load does what the uncached system did continuously"
    ),
    Slack.RETRY: (
        "a failing dependency now receives more traffic than before, which is the feedback "
        "loop `00-BRIEF.md` §4 names first"
    ),
    Slack.CONNECTION_REUSE: (
        "connections are held rather than re-established, so a restart or a failover has to "
        "rebuild state the old code rebuilt continuously"
    ),
    Slack.POOL_SHRUNK: (
        "fewer concurrent operations can be in flight, so a burst queues where it used to proceed"
    ),
    Slack.TIMEOUT_SHRUNK: (
        "work that used to complete slowly now fails, and whatever handles that failure "
        "becomes load-bearing under exactly the conditions that caused it"
    ),
    Slack.BUFFERING: (
        "work is held before it is done, so memory grows with arrival rate and a backlog "
        "outlives the burst that created it"
    ),
}


_ADDED_PATTERNS: tuple[tuple[Slack, re.Pattern[str]], ...] = (
    (
        Slack.CACHE,
        re.compile(
            r"lru_cache|cached_property|\bmemoi[sz]|functools\.cache|@cache\b|"
            r"cache\.set\(|cache_page|get_or_set|\bcache\s*=|_cache\b",
            re.IGNORECASE,
        ),
    ),
    (
        Slack.RETRY,
        re.compile(
            # `backoff` carries no word boundary on purpose: the identifier that
            # names it is usually compound — `adapter_with_backoff`,
            # `retry_backoff_ms` — and `\b` does not match against an underscore,
            # so the boundary version missed the ordinary spelling.
            r"\bretry|\bretries\b|backoff|\btenacity\b|max_attempts|\breattempt",
            re.IGNORECASE,
        ),
    ),
    (
        Slack.CONNECTION_REUSE,
        re.compile(
            r"CONN_MAX_AGE|keep[_-]?alive|pool_pre_ping|connection_pool|persistent_connection|"
            r"requests\.Session\(",
            re.IGNORECASE,
        ),
    ),
    (
        Slack.BUFFERING,
        re.compile(r"\bbuffer|bufsize|buffering\s*=", re.IGNORECASE),
    ),
)

_ASSIGNMENT = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_.]*)\s*[=:]\s*(?P<value>\d+(?:\.\d+)?)")
"""Any name assigned a number. **Deliberately not one regex with the vocabulary
embedded**, which is how the first draft was written and how it silently matched
nothing: a leading `[A-Za-z_]` consumes the `P` of `POOL_SIZE`, leaving `OOL_SIZE`
for the alternation to find `pool_size` in, which it never can. Finding
assignments and deciding which ones are settings are two questions, and one regex
answering both answered neither."""

_SETTING_NAME = re.compile(
    r"timeout|pool_size|maxsize|max_size|pool_recycle|max_connections|max_overflow",
    re.IGNORECASE,
)

_TIMEOUT_NAME = re.compile(r"timeout", re.IGNORECASE)


@dataclass(frozen=True)
class Removal:
    """One place the patch takes headroom away."""

    kind: Slack
    evidence: str
    """The added line, or the before-and-after of a setting. Quoted so the warning
    can name *where*, which is what makes F1's warning specific rather than a
    banner nobody reads twice."""

    def describe(self) -> str:
        return f"{self.kind.value} — {self.evidence}\n      removes: {self.kind.headroom}"


@dataclass(frozen=True)
class Classification:
    """What the diff was found to remove, and what may be done about it."""

    removals: tuple[Removal, ...]
    amplification: Amplification | None = None

    @property
    def slack_reducing(self) -> bool:
        return bool(self.removals)

    @property
    def label(self) -> str | None:
        """AC 2. `None` where nothing matched — **never** a second label meaning
        *checked and clean*, because this classifier cannot establish that."""
        return LABEL if self.slack_reducing else None

    @property
    def kinds(self) -> tuple[Slack, ...]:
        seen: list[Slack] = []
        for removal in self.removals:
            if removal.kind not in seen:
                seen.append(removal.kind)
        return tuple(seen)

    def warning(self) -> str:
        """AC 4. F1's staging warning, naming what headroom was removed."""
        if not self.slack_reducing:
            return ""
        lines = ["This patch removes headroom:"]
        lines.extend(f"    - {removal.describe()}" for removal in self.removals)
        lines.append(f"  {STAGING_WARNING}")
        return "\n".join(lines)

    def describe(self) -> str:
        if not self.slack_reducing:
            return (
                "No slack-reducing pattern matched this diff. That is not a clean bill of "
                f"health.\n  {RESIDUE}"
            )
        lines = [
            f"LABEL: {LABEL} — this patch can never be auto-approved, at any trust level.",
            self.warning(),
        ]
        if self.amplification is not None:
            lines.append(f"  Retry amplification: {self.amplification.explanation()}")
        else:
            lines.append(
                "  Retry amplification was not checked. S-3.16 needs a live subject and an "
                "injectable dependency; where neither is available this is unmeasured rather "
                "than absent."
            )
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)


def classify(diff: str, *, amplification: Amplification | None = None) -> Classification:
    """Find every place the diff removes headroom. AC 1, AC 2 and AC 5.

    `amplification` is S-3.16's result where a caller was able to run it. It is
    **supplied rather than measured** — this module has no subject, no dependency
    to degrade and no way to drive one — which is S-9.2's construction for a
    missing fit, and its absence is reported as unmeasured rather than passed
    over.
    """
    lines = hunk_lines(diff)
    added = [content for marker, content in lines if marker == "+"]
    removed = [content for marker, content in lines if marker == "-"]

    removals: list[Removal] = [
        Removal(kind=kind, evidence=content.strip())
        for content in added
        for kind, pattern in _ADDED_PATTERNS
        if pattern.search(content)
    ]
    removals.extend(_shrunk(removed, added))
    return Classification(removals=tuple(removals), amplification=amplification)


def _shrunk(removed: Sequence[str], added: Sequence[str]) -> list[Removal]:
    """Settings whose value went **down**. The two patterns that are comparisons.

    Matched by name rather than by position within the hunk. Git puts removed
    lines before added ones, but a hunk that reorders or reindents makes
    position meaningless, and a name-keyed comparison explains itself in the
    report: `pool_size 20 -> 5` is a sentence, *the third minus line* is not.

    A setting that went **up** is deliberately not a removal. Raising a timeout
    adds headroom, and flagging it would teach every reader to ignore the label.
    """
    before = _settings(removed)
    after = _settings(added)

    found: list[Removal] = []
    for name in sorted(before.keys() & after.keys()):
        if after[name] >= before[name]:
            continue
        kind = Slack.TIMEOUT_SHRUNK if _TIMEOUT_NAME.search(name) else Slack.POOL_SHRUNK
        found.append(Removal(kind=kind, evidence=f"{name} {before[name]:g} -> {after[name]:g}"))
    return found


def _settings(lines: Sequence[str]) -> dict[str, float]:
    """Every pool-or-timeout-shaped setting assigned a number, by name.

    The **last** assignment wins where a name appears twice, which matches how
    the file would read. A diff that sets one name twice on the added side is
    unusual enough that reporting the final value is the honest reading.
    """
    values: dict[str, float] = {}
    for line in lines:
        for match in _ASSIGNMENT.finditer(line):
            name = match.group("name")
            if _SETTING_NAME.search(name):
                values[name] = float(match.group("value"))
    return values


def may_auto_approve(classification: Classification) -> bool:
    """AC 3. **There is no trust-level parameter, and that is the enforcement.**

    F1: *block auto-approval permanently — no trust level can clear it.* A
    function taking a level would be a function somebody could pass a high enough
    one to, and Epic 14's ledger does not exist yet to be argued with. The
    construction S-9.1 used for `chain` and S-10.1 for `diff`: the way to make a
    thing impossible is to leave nowhere to put it.
    """
    return not classification.slack_reducing


def patterns() -> Sequence[tuple[str, str]]:
    """Every pattern and what it removes, for a prompt, a report or a reviewer.

    Enumerable rather than something a reader has to notice, which is
    `dispositions()`'s argument in S-5.4 and `catalogue()`'s in S-10.1.
    """
    return [(item.name.lower(), item.headroom) for item in Slack]
