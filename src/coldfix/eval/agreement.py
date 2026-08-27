"""Diagnose one repository ten times and report how often the answer was the same.

Epic 15, S-15.1. `00-BRIEF.md` §6 calls this *the honest form of "reliable", and
nobody publishes it for this domain* — which is the whole reason it is the
headline number rather than a success rate. A tool that finds the right cause
seven times out of ten and a different one three times is not seventy percent
right; it is a tool whose next answer you cannot predict.

**It runs nothing.** The observations come from the harness, the same rule
`eval/ablation.py` and `eval/learning.py` follow: `CLAUDE.md` forbids a study
that takes its own measurements, and a study that drove the pipeline itself could
not be re-run against recorded results without spending the corpus again.

## The three ways this number is quietly wrong

**Measured with the cache on, it is 100% and means nothing.** S-5.1's replay
cache keys on `(repo_sha, workload_id, experiment_spec, fixture_hash)` and returns
the recorded answer, so ten cached runs are one run reported ten times. This is
not a weaker measurement of agreement — it is a measurement of the cache. So a
run that was served from the cache is **refused**, not down-weighted, and the
artifact cannot be constructed over one.

**A null result is an outcome, not an exclusion.** Nine runs that found nothing
and one that found a cause do not agree 100% on that cause; they agree 90% that
there is nothing to find, and the disagreement is the interesting part. Dropping
the nulls would report perfect agreement over a single run. `None` is a key in
the distribution like any other.

**A finding key has to be something measured.** Two runs describing one cause in
different words are not a disagreement, and two different causes described alike
are not an agreement — so the key must come from a measurement, which for this
system is the causal site `primitives.localization` walks to. That is the
caller's to derive, for the reason `factory_seeder`'s module path is: this module
cannot know how the caller identifies a finding, and guessing would put a
judgement where a fact belongs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from coldfix.eval.ablation import wilson

MINIMUM_RUNS = 10
"""AC 4, and the reason it is a floor rather than advice.

At five runs a single flip moves the rate by twenty points and the Wilson
interval spans most of the unit interval, so the figure cannot distinguish a tool
that is reliable from one that is not — which is the only question it exists to
answer.
"""


class AgreementError(Exception):
    """An agreement study could not be assembled from these runs."""


@dataclass(frozen=True)
class Run:
    """One independent diagnosis of one repository.

    Independent in the sense that matters: a fresh investigation that reached its
    own conclusion. Two records of the same run are refused, because the second
    one inflates both the denominator and the agreement.
    """

    run_id: str
    finding: str | None
    """What this run concluded, keyed on something measured — a causal site, not
    the agent's wording. `None` is *nothing found*, which is an answer."""

    served_from_cache: bool = False
    """Whether S-5.1's replay cache answered any part of this run.

    Defaults to `False` because the overwhelming majority of runs are not cached
    and a required field would be noise at every call site — but a `True` is
    refused outright rather than recorded, so the default cannot quietly become
    the guarantee.
    """

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            message = "a run needs an id, or two runs cannot be told apart"
            raise AgreementError(message)


@dataclass(frozen=True)
class Agreement:
    """How often ten independent diagnoses of one repository said the same thing."""

    repository: str
    runs: tuple[Run, ...]

    @property
    def distribution(self) -> Mapping[str | None, int]:
        """AC 3. Every distinct outcome and how many runs reached it.

        Includes `None` where runs found nothing. A distribution over findings
        only would describe a different experiment — one in which the runs that
        disagreed about whether anything is there were never conducted.
        """
        counts: dict[str | None, int] = {}
        for run in self.runs:
            counts[run.finding] = counts.get(run.finding, 0) + 1
        return counts

    @property
    def modal_outcomes(self) -> tuple[str | None, ...]:
        """Every outcome tied for most frequent, in a stable order.

        A tuple rather than a single value, because the tie is real: five runs
        saying one thing and five saying another have no primary finding, and
        naming either would invent a winner from an arithmetic accident.
        """
        counts = self.distribution
        most = max(counts.values())
        leaders = [outcome for outcome, count in counts.items() if count == most]
        return tuple(sorted(leaders, key=lambda outcome: (outcome is not None, outcome or "")))

    @property
    def undecided(self) -> bool:
        """Whether two or more outcomes tie for most frequent."""
        return len(self.modal_outcomes) > 1

    @property
    def primary(self) -> str | None:
        """AC 2's subject: the outcome most runs reached. `None` means *nothing found*.

        Raises:
            AgreementError: the runs tie, so there is no primary outcome. Raised
                rather than returning one of them, because `None` already means
                *nothing found was the modal answer* and a tie is a third thing.
                Read `modal_outcomes` when this raises.
        """
        if self.undecided:
            message = (
                f"{len(self.modal_outcomes)} outcomes tie at {self.agreeing} run(s) each on "
                f"{self.repository!r}, so there is no primary finding. That is the result, not "
                "a gap in it — see `modal_outcomes`"
            )
            raise AgreementError(message)
        return self.modal_outcomes[0]

    @property
    def agreeing(self) -> int:
        """How many runs reached the most frequent outcome."""
        return max(self.distribution.values())

    @property
    def rate(self) -> float:
        """AC 2. The share of runs that reached the most frequent outcome."""
        return self.agreeing / len(self.runs)

    @property
    def interval(self) -> tuple[float, float]:
        """A Wilson interval on the rate, because ten runs is not many.

        Eight out of ten is 80% with a 95% interval of roughly 49% to 94%, and
        publishing the point estimate alone would let a reader take it for a
        measurement of a system rather than of ten runs of one.
        """
        return wilson(self.agreeing, len(self.runs))

    @property
    def flipped(self) -> bool:
        """Whether the runs reached more than one outcome at all.

        S-15.4's failure catalogue records *diagnoses that flipped between runs*,
        and this is that fact. Distinct from a low rate: two outcomes at 9-1 have
        flipped, and a reader who only saw 90% would not know it.
        """
        return len(self.distribution) > 1

    def render(self) -> str:
        low, high = self.interval
        lines = [
            f"Diagnostic agreement on {self.repository}: {len(self.runs)} independent runs",
        ]

        if self.undecided:
            named = ", ".join(_name(outcome) for outcome in self.modal_outcomes)
            lines.append(
                f"  no primary finding: {len(self.modal_outcomes)} outcomes tie at "
                f"{self.agreeing} run(s) each — {named}. **That is the result.** A tool whose "
                "answer is decided by which run you look at has not been shown to have one."
            )
        else:
            lines.append(
                f"  primary: {_name(self.primary)} — {self.agreeing}/{len(self.runs)} runs "
                f"({self.rate:.0%}, 95% CI {low:.0%} to {high:.0%})"
            )

        lines.append("  every outcome reached:")
        lines.extend(
            f"    {count:>3} x {_name(outcome)}"
            for outcome, count in sorted(
                self.distribution.items(),
                key=lambda item: (-item[1], item[0] is not None, item[0] or ""),
            )
        )

        if self.flipped:
            lines.append(
                f"  the diagnosis flipped: {len(self.distribution)} distinct outcomes across "
                "these runs. The point estimate above is the share of the most common one, not "
                "a probability that it is correct."
            )
        else:
            lines.append("  every run reached the same outcome.")
        return "\n".join(lines)


def _name(outcome: str | None) -> str:
    return "nothing found" if outcome is None else outcome


def agreement(repository: str, runs: Sequence[Run]) -> Agreement:
    """Assemble one agreement study from independently conducted runs.

    Raises:
        AgreementError: fewer than `MINIMUM_RUNS` runs, two runs sharing an id,
            or a run the replay cache answered. Each is a reason the figure would
            not mean what it says rather than a reason it would be imprecise.
    """
    if len(runs) < MINIMUM_RUNS:
        message = (
            f"{len(runs)} run(s) is below the {MINIMUM_RUNS} this figure needs. At five runs a "
            "single flip moves the rate twenty points and the interval spans most of the unit "
            "interval, which cannot distinguish a reliable tool from an unreliable one"
        )
        raise AgreementError(message)

    seen: set[str] = set()
    for run in runs:
        if run.run_id in seen:
            message = (
                f"run {run.run_id!r} appears twice. One run recorded twice raises both the "
                "denominator and the count that agrees with itself, so the figure improves for "
                "a reason that is not about the tool"
            )
            raise AgreementError(message)
        seen.add(run.run_id)

    cached = sorted(run.run_id for run in runs if run.served_from_cache)
    if cached:
        message = (
            f"{cached} were served from the replay cache. Ten cached runs are one run reported "
            "ten times, so this would measure the cache rather than the tool — S-15.1's "
            "criterion is *with cache disabled*, and that is refused here rather than noted"
        )
        raise AgreementError(message)

    return Agreement(repository=repository, runs=tuple(runs))
