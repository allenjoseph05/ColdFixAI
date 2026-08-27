"""Run a stated subset of SWE-Perf, and report it per category.

Epic 15, S-15.2. `00-BRIEF.md` §6 lists capability as *SWE-Perf instances,
per-category, against expert patches*, and `02-architecture.md` §8 puts the
corpus at 140 instances with expert patches to compare against.

**It runs nothing**, the rule the other three `eval/` modules follow: the
observations come from the harness, and a study that drove the pipeline itself
could not be re-run against recorded results without paying for the corpus again.

## The categories are the dataset's, not ours

`Instance` takes the category it belongs to. This project has no opinion about
how SWE-Perf partitions its instances, and inventing a taxonomy here would
produce a per-category report about categories nobody else uses — which is the
opposite of *comparable against expert patches*.

## An uncertified improvement is not an improvement

`05-research.md` §10.4 is the constraint, and it is a finding about benchmarks of
exactly this kind. A 2026 audit of GSO, SWE-Perf and SWE-fficiency found that
runtime measurements are not fixed quantities — the same patch appears faster,
slower, or statistically unsupported depending on where it is replayed — **and
those benchmarks already use repeated trials, outlier filtering, statistical
tests and reference patches.** The direct implication it draws: a harness must
treat statistical certification as a first-class requirement rather than a
refinement.

So a result carries its `Certification`, and one whose harness could not resolve
an effect the size of the one claimed is recorded as **unresolved** rather than
as a win or a loss. That is a third outcome, and collapsing it into either of the
other two is how a benchmark number comes to describe the machine it ran on.

## Reported per category, never as one number

AC 3 says per-category and not aggregate, and the reason is the same one
`08-audit.md` gives about ranking across kinds: a single percentage over a corpus
of unlike instances is an average of things measured on different scales. The
report has no total, and `Benchmark` exposes no property that produces one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.certification import Certification


class BenchmarkError(Exception):
    """A benchmark run could not be assembled from these results."""


class Standing(StrEnum):
    """How one attempt compares with the expert patch for the same instance.

    Four, and the fourth is the one §10.4 forces. *Unresolved* is not a weaker
    loss: it says the instrument could not see an effect the size of the one
    being claimed, so nothing about this instance was measured — which is a fact
    about the harness rather than about the patch.
    """

    MATCHED = "reached the expert patch's speedup or better"
    SHORT = "improved, but by less than the expert patch"
    NO_CHANGE = "produced no improvement this harness could see"
    UNRESOLVED = "the harness could not resolve an effect this size"


@dataclass(frozen=True)
class Instance:
    """One SWE-Perf instance, as the dataset describes it."""

    instance_id: str
    category: str
    """The dataset's own partition. See the module docstring for why this is not
    an enum defined here."""

    expert_speedup: float
    """What the expert patch achieved, as a ratio above 1.0."""

    def __post_init__(self) -> None:
        if self.expert_speedup <= 1.0:
            message = (
                f"{self.instance_id!r} records an expert speedup of {self.expert_speedup}. An "
                "instance whose expert patch did not make the program faster is not a target "
                "this benchmark can be scored against"
            )
            raise BenchmarkError(message)
        if not self.category.strip():
            message = f"{self.instance_id!r} has no category, and AC 3 reports per category"
            raise BenchmarkError(message)


@dataclass(frozen=True)
class Result:
    """What this system produced for one instance, and whether it can be believed.

    **`certification` is required.** §10.4's whole finding is that a speedup
    reported without one describes the machine as much as the patch, and a field
    that could be `None` is a field most callers would leave out.
    """

    instance: Instance
    speedup: float | None
    """What the patch achieved, or `None` where no patch was produced.

    `None` is not 1.0. *Nothing was proposed* and *something was proposed and
    changed nothing* are different results, and a benchmark that recorded them
    alike would credit a system that never answered with a null improvement.
    """

    certification: Certification

    @property
    def standing(self) -> Standing:
        """Where this sits against the expert patch.

        **Certification is checked first**, before the numbers are compared at
        all. A speedup the harness could not resolve is not a small speedup; it
        is an unread instrument, and comparing it against the expert's figure
        would be arithmetic over a number nobody measured.
        """
        if not self.certification.certified:
            return Standing.UNRESOLVED
        if self.speedup is None or self.speedup <= 1.0:
            return Standing.NO_CHANGE
        if self.speedup >= self.instance.expert_speedup:
            return Standing.MATCHED
        return Standing.SHORT

    def describe(self) -> str:
        achieved = "no patch" if self.speedup is None else f"{self.speedup:.2f}x"
        return (
            f"{self.instance.instance_id}: {achieved} against the expert's "
            f"{self.instance.expert_speedup:.2f}x — {self.standing.value}"
        )


@dataclass(frozen=True)
class Category:
    """One category's results. The unit this benchmark reports in."""

    name: str
    results: tuple[Result, ...]

    def counted(self, standing: Standing) -> int:
        return sum(1 for result in self.results if result.standing is standing)

    @property
    def resolved(self) -> int:
        """Instances whose harness could see an effect the size being claimed.

        The denominator for every rate below. An unresolved instance is not a
        failure to match; it is an instance this benchmark did not measure.
        """
        return len(self.results) - self.counted(Standing.UNRESOLVED)

    @property
    def matched_rate(self) -> float | None:
        """Matched over *resolved*, or `None` when nothing resolved.

        `None` rather than zero, for the reason `RunReport` gives about a run
        that confirmed nothing: the instances were attempted and the ratio is
        undefined, which is a different statement from *none matched*.
        """
        if self.resolved == 0:
            return None
        return self.counted(Standing.MATCHED) / self.resolved

    def describe(self) -> str:
        rate = self.matched_rate
        head = f"{self.name}: {len(self.results)} instance(s)"
        if rate is None:
            head += " — none resolved, so there is no rate to report"
        else:
            head += (
                f", {self.resolved} resolved, "
                f"{self.counted(Standing.MATCHED)} matched the expert ({rate:.0%})"
            )
        lines = [head]
        unresolved = self.counted(Standing.UNRESOLVED)
        if unresolved:
            lines.append(
                f"  {unresolved} unresolved: the harness could not see an effect this size, so "
                "these are excluded from the rate rather than counted as failures"
            )
        lines.extend(f"  {result.describe()}" for result in self.results)
        return "\n".join(lines)


@dataclass(frozen=True)
class Benchmark:
    """A run of a stated subset, reported per category. AC 3 and AC 4.

    **There is no aggregate and no property that produces one.** A single
    percentage over a corpus of unlike instances averages measurements taken on
    different scales, which is `08-audit.md`'s argument about ranking across
    kinds applied to scoring across categories.
    """

    corpus: str
    corpus_size: int
    """How many instances the whole dataset holds. AC 4's denominator: a subset
    is only *stated openly* if a reader can see what it is a subset of."""

    selection: str
    """Why these instances and not others, in the author's own words."""

    results: tuple[Result, ...]

    def __post_init__(self) -> None:
        if not self.results:
            message = (
                f"a benchmark over no instances of {self.corpus!r}. Nothing attempted and "
                "nothing achieved are different results"
            )
            raise BenchmarkError(message)
        if not self.selection.strip():
            message = (
                "AC 4 asks for the selection criteria stated openly, and a subset whose "
                "criteria are unstated is one a reader cannot tell from a subset chosen "
                "after the numbers were seen"
            )
            raise BenchmarkError(message)
        if len(self.results) > self.corpus_size:
            message = (
                f"{len(self.results)} results against a corpus of {self.corpus_size}. One of "
                "the two is wrong, and a subset larger than its corpus makes every rate below "
                "unreadable"
            )
            raise BenchmarkError(message)

    @property
    def categories(self) -> tuple[Category, ...]:
        grouped: dict[str, list[Result]] = {}
        for result in self.results:
            grouped.setdefault(result.instance.category, []).append(result)
        return tuple(
            Category(name=name, results=tuple(found)) for name, found in sorted(grouped.items())
        )

    def render(self) -> str:
        lines = [
            f"{self.corpus}: {len(self.results)} of {self.corpus_size} instance(s), "
            f"in {len(self.categories)} categor{'y' if len(self.categories) == 1 else 'ies'}",
            f"  selection: {self.selection}",
            "",
            "Reported per category and never as one number: a single rate over unlike "
            "instances averages measurements taken on different scales.",
            "",
        ]
        lines.extend(category.describe() for category in self.categories)
        return "\n".join(lines)


def benchmark(
    *,
    corpus: str,
    corpus_size: int,
    selection: str,
    results: Sequence[Result],
) -> Benchmark:
    """Assemble one benchmark run from results the harness recorded.

    Raises:
        BenchmarkError: no results, an unstated selection, a subset larger than
            its corpus, or two results for one instance.
    """
    seen: set[str] = set()
    for result in results:
        identifier = result.instance.instance_id
        if identifier in seen:
            message = (
                f"instance {identifier!r} appears twice. One instance scored twice moves the "
                "rate for a reason that is not about the system"
            )
            raise BenchmarkError(message)
        seen.add(identifier)

    return Benchmark(
        corpus=corpus,
        corpus_size=corpus_size,
        selection=selection,
        results=tuple(results),
    )


def standings(results: Sequence[Result]) -> Mapping[Standing, int]:
    """How many results reached each standing. For a caller reporting elsewhere."""
    counts: dict[Standing, int] = {}
    for result in results:
        counts[result.standing] = counts.get(result.standing, 0) + 1
    return counts
