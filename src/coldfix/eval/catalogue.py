"""The failures, published. `00-BRIEF.md`: more credible than the success rate.

Epic 15, S-15.4. Four kinds of negative result, each carrying the evidence that
makes it checkable rather than a sentence saying it happened:

| what | evidence | from |
|---|---|---|
| a repository where nothing was found | the null result, thresholds and all | S-4.5 |
| a cheat somebody caught | the diff, and the attack that caught it | Epic 11 |
| a diagnosis that flipped between runs | the agreement study and its distribution | S-15.1 |
| a grounding that failed | the stage that never completed, and what was tried there | S-7.10 |

**Nothing here is a claim this module makes.** Every entry holds an artifact
another epic produced under its own rules — a `NullResult` cannot be constructed
from a screen that flagged something, an `AttackResult` that landed cannot be
constructed without the text a reader acts on — so a catalogue entry is a
reference to a measurement rather than a report of one.

## The two ways a failure catalogue lies

**By omission**, which is the obvious one and the reason the document exists.

**By padding**, which is not. An entry recording a cheat nobody caught, or a
diagnosis that did not flip, is a failure that did not happen — and a catalogue
whose credibility comes from being uncomfortable is destroyed as thoroughly by
inventing discomfort as by hiding it. So `CaughtCheat` refuses an attack that
passed and `FlippedDiagnosis` refuses a study that agreed.

## An empty catalogue is the least credible artifact here, not the most

Empty means one of two things and they are opposite: nothing has been run, or
runs were catalogued and none of them failed. The catalogue cannot tell those
apart from its entries, so it carries `runs_covered` and says which — the same
distinction S-4.5 makes between *screened nine workloads, nothing found* and
*nothing was screened*.

A catalogue over zero runs is refused outright. It is not an encouraging result;
it is not a result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from coldfix.audit.patchverdict import AttackResult
from coldfix.eval.agreement import Agreement
from coldfix.explorer.run import Failure
from coldfix.screening.null import NullResult


class CatalogueError(Exception):
    """An entry could not be recorded, or a catalogue could not be assembled."""


@dataclass(frozen=True)
class NothingFound:
    """A repository screened, with nothing worth investigating in it. AC 1.

    Holds the `NullResult` rather than a sentence, because S-4.5 put the
    thresholds, the per-workload conditions and the measured basis on that
    artifact — and *nothing found* means one thing across a sixteenfold sweep of
    uniform fixtures and something much weaker otherwise.
    """

    repository: str
    result: NullResult

    @property
    def covers_everything(self) -> bool:
        """Whether *nothing found* is a statement about every workload looked at."""
        return self.result.covers_everything_screened

    def describe(self) -> str:
        head = f"{self.repository}: nothing found across {len(self.result.screened)} workload(s)"
        if not self.covers_everything:
            head += " — and the result does not cover all of them"
        return f"{head}\n{_indent(self.result.report())}"


@dataclass(frozen=True)
class CaughtCheat:
    """A patch that improved the metric without improving the program. AC 2.

    **The diff and the attack are both required**, which is the criterion read
    literally and also the only form of this entry worth publishing: *the
    Adversary caught three cheats* is a claim, and *here is the diff and here is
    the attack that caught it* is a thing somebody can check.
    """

    repository: str
    finding: str
    diff: str
    caught_by: AttackResult

    def __post_init__(self) -> None:
        if not self.diff.strip():
            message = (
                f"a caught cheat on {self.finding!r} with no diff. AC 2 asks for the diff and "
                "the attack because a catalogue of cheats nobody can read is a success claim "
                "with the sign flipped"
            )
            raise CatalogueError(message)
        if not self.caught_by.landed:
            message = (
                f"{self.caught_by.attack.value} reports {self.caught_by.outcome.value}, so it "
                "caught nothing. Recording it here would put a failure that did not happen in "
                "the document whose whole credibility is that its entries are uncomfortable"
            )
            raise CatalogueError(message)

    def describe(self) -> str:
        lines = [
            f"{self.repository}: a cheat on {self.finding}, caught by "
            f"{self.caught_by.attack.value}",
            _indent(self.caught_by.describe()),
            _indent(self.diff.strip()),
        ]
        if self.caught_by.reproduction is not None:
            lines.append(_indent("reproduction: run it and see it again"))
        return "\n".join(lines)


@dataclass(frozen=True)
class FlippedDiagnosis:
    """One repository, several runs, more than one answer. AC 3.

    The number `00-BRIEF.md` §6 calls the honest form of *reliable*, recorded
    where it is uncomfortable rather than only where it is good.
    """

    repository: str
    study: Agreement

    def __post_init__(self) -> None:
        if not self.study.flipped:
            message = (
                f"every run of {self.repository!r} reached the same outcome, so nothing "
                "flipped. An agreement study that agreed is a result and it is not a failure"
            )
            raise CatalogueError(message)

    def describe(self) -> str:
        return f"{self.repository}:\n{_indent(self.study.render())}"


@dataclass(frozen=True)
class FailedGrounding:
    """A repository the Explorer could not stand up, and where it stopped. AC 4.

    Holds S-7.10's `Failure`, which leads with the stage that never completed,
    what *done* would have meant there, and everything tried — because *grounding
    failed* is not actionable and *stage four never completed, here is its
    predicate and the last error* is.
    """

    repository: str
    failure: Failure

    def describe(self) -> str:
        return f"{self.repository}:\n{_indent(self.failure.report())}"


@dataclass(frozen=True)
class Catalogue:
    """Every negative result this evaluation produced, with its evidence. AC 5.

    `runs_covered` is required and is the difference between the two things an
    empty catalogue can mean. See the module docstring.
    """

    runs_covered: int
    nothing_found: tuple[NothingFound, ...] = ()
    cheats: tuple[CaughtCheat, ...] = ()
    flipped: tuple[FlippedDiagnosis, ...] = ()
    groundings: tuple[FailedGrounding, ...] = ()

    def __post_init__(self) -> None:
        if self.runs_covered < 1:
            message = (
                "a catalogue over no runs. Nothing catalogued and nothing run are different "
                "answers, and only one of them is a result — S-4.5's rule, one layer out"
            )
            raise CatalogueError(message)

    @property
    def entries(self) -> int:
        return len(self.nothing_found) + len(self.cheats) + len(self.flipped) + len(self.groundings)

    @property
    def empty(self) -> bool:
        """Whether nothing at all was recorded. **Never read this as good news.**

        Over a handful of runs it means the evaluation is young. Over many it is
        a claim that wants explaining, because every other measurement in this
        project says some of these happen.
        """
        return self.entries == 0

    def render(self) -> str:
        lines = [
            f"Failure catalogue: {self.entries} entr{'y' if self.entries == 1 else 'ies'} "
            f"across {self.runs_covered} run(s)",
            "",
            "`00-BRIEF.md` §6: this is more credible than the success rate, which is why it "
            "is published beside the results rather than on request.",
        ]

        if self.empty:
            lines.extend(
                [
                    "",
                    f"**Nothing was recorded across {self.runs_covered} run(s), and that is not "
                    "a result to be pleased about.** It means either that these runs produced "
                    "no null results, no caught cheats, no flipped diagnoses and no failed "
                    "groundings — which every other measurement in this project says is "
                    "unlikely — or that nobody recorded them. The catalogue cannot tell those "
                    "apart and does not guess.",
                ]
            )
            return "\n".join(lines)

        for title, described in (
            ("Repositories where nothing was found", self.nothing_found),
            ("Cheats that were caught", self.cheats),
            ("Diagnoses that flipped between runs", self.flipped),
            ("Groundings that failed", self.groundings),
        ):
            if not described:
                continue
            lines.extend(["", f"{title} ({len(described)}):"])
            lines.extend(_indent(entry.describe()) for entry in described)
        return "\n".join(lines)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(f"{prefix}{line}" if line.strip() else line for line in text.splitlines())


def catalogue(
    # independent facts from four different epics; bundling any two would invent a
    # type whose only purpose is to be unpacked.
    *,
    runs_covered: int,
    nothing_found: Sequence[NothingFound] = (),
    cheats: Sequence[CaughtCheat] = (),
    flipped: Sequence[FlippedDiagnosis] = (),
    groundings: Sequence[FailedGrounding] = (),
) -> Catalogue:
    """Assemble one catalogue from entries other epics produced.

    Keyword-only, because five sequences of different entry types in a row is a
    call nobody reads correctly twice.

    Raises:
        CatalogueError: no runs are covered.
    """
    return Catalogue(
        runs_covered=runs_covered,
        nothing_found=tuple(nothing_found),
        cheats=tuple(cheats),
        flipped=tuple(flipped),
        groundings=tuple(groundings),
    )
