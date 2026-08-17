"""How many findings one run may investigate, and what happens to the rest.

Epic 4, S-4.4. `04-cost.md` §12.4 states the case in one line: *without this cap,
every figure above is meaningless — the worst case is simply unbounded.* A
repository where screening flags thirty workloads costs thirty investigations,
and an investigation is the expensive phase.

**The cap counts workloads, not flags.** One workload can be flagged on three
metrics — a query count that climbed, a payload that climbed with it, a duration
that cleared the floor — and those are one investigation, not three. Grounding,
fixture seeding and the baseline are per workload and shared across everything
flagged on it. Counting flags would let a single workload with five flagged
metrics consume an entire run's budget while nine others were never looked at.

**Nothing is dropped, and the distinction matters more here than anywhere.**
S-4.3 attaches a sentence to every ranking saying that call frequency is unknown
and the ordering cannot express which finding matters more. In a report that
caveat costs a reader a moment's care. **At the cap it decides what gets
investigated at all**, so the deferral list repeats it: these are the workloads a
magnitude ordering put below the line, and magnitude is not importance.

**Configurable, with a ceiling in code.** The story asks for a configurable
default of five. A cap a caller can set to a thousand is not a cap, and
`04-cost.md` §12.1 puts a worst-case finding at about $58 — so the ceiling here
is what keeps the guarantee the story exists for.
"""

from __future__ import annotations

from dataclasses import dataclass

from coldfix.screening.flagging import FREQUENCY_UNKNOWN, Flag, Ranking

# `04-cost.md` §12.4's number, and the story's default.
DEFAULT_FINDINGS_CAP = 5

# The ceiling a caller cannot raise past. At §12.1's worst-case ~$58 per finding
# this is already over a thousand dollars for one run, and a repository needing
# more than twenty investigations in one pass is one to split rather than one to
# spend on — the twenty-first finding is written against source the first twenty
# may have changed, which `08-audit.md` §6 flags separately.
MAXIMUM_FINDINGS_CAP = 20


class BudgetError(Exception):
    """A run's investigation budget could not be set."""


@dataclass(frozen=True)
class Deferral:
    """A flagged workload this run will not investigate, and where it placed."""

    workload_id: str
    position: int
    """Its place in the magnitude ordering, counting from one."""

    flags: tuple[Flag, ...]

    def summary(self) -> str:
        metrics = ", ".join(sorted({flag.metric for flag in self.flags}))
        best = max(flag.magnitude for flag in self.flags)
        return f"#{self.position} {self.workload_id} — {metrics}, worst {best:.1f}x"


@dataclass(frozen=True)
class Plan:
    """What one run will investigate, and everything it will not.

    Four lists rather than two, because they call for four different things from
    whoever reads them: investigate these, look at these yourself, these were
    measured and are fine, and these could not be classified at all.
    """

    investigate: tuple[str, ...]
    deferred: tuple[Deferral, ...]
    healthy: tuple[str, ...]
    unclassified: tuple[tuple[str, str], ...]
    cap: int

    @property
    def within_budget(self) -> bool:
        """Whether every flagged workload fits under the cap.

        `False` means the run is deliberately not looking at things it found,
        which is a fact a reader needs before treating the result as complete.
        """
        return not self.deferred

    def report(self) -> str:
        head = (
            f"Investigating {len(self.investigate)} of "
            f"{len(self.investigate) + len(self.deferred)} flagged workloads, capped at "
            f"{self.cap}."
        )
        if self.within_budget:
            return f"{head} Nothing was deferred."

        listed = "\n".join(deferral.summary() for deferral in self.deferred)
        return (
            f"{head} **{len(self.deferred)} flagged workloads were not investigated and are "
            "listed below rather than dropped.** The line between them and the ones above it "
            "was drawn by measured magnitude, and "
            f"{FREQUENCY_UNKNOWN}\n{listed}"
        )


def plan(ranking: Ranking, *, cap: int = DEFAULT_FINDINGS_CAP) -> Plan:
    """Decide which flagged workloads this run investigates.

    Workloads are taken in the order S-4.3 ranked them and the rest are deferred.
    A workload flagged on several metrics is one entry: an investigation grounds
    and seeds once, and everything flagged on that workload is investigated by
    the same run.

    Raises:
        BudgetError: a cap below one, which investigates nothing while still
            paying for the screen, or above `MAXIMUM_FINDINGS_CAP`, which is not
            a cap.
    """
    if cap < 1:
        message = (
            f"a cap of {cap} investigates nothing, having already paid for the screen. If the "
            "intention is to screen without investigating, say so at the call site rather than "
            "by setting a budget to zero"
        )
        raise BudgetError(message)
    if cap > MAXIMUM_FINDINGS_CAP:
        message = (
            f"a cap of {cap} is above the {MAXIMUM_FINDINGS_CAP} this system will run in one "
            f"pass. `04-cost.md` §12.1 puts a worst-case finding at about $58, so this is a run "
            "with no ceiling on it — which is the condition §12.4 says makes every cost figure "
            "in that document meaningless. Split the repository across runs instead"
        )
        raise BudgetError(message)

    ordered = _workloads_in_rank_order(ranking)
    return Plan(
        investigate=tuple(workload for workload, _ in ordered[:cap]),
        deferred=tuple(
            Deferral(workload_id=workload, position=position, flags=flags)
            for position, (workload, flags) in enumerate(ordered[cap:], start=cap + 1)
        ),
        healthy=ranking.healthy,
        unclassified=ranking.unclassified,
        cap=cap,
    )


def _workloads_in_rank_order(ranking: Ranking) -> list[tuple[str, tuple[Flag, ...]]]:
    """One entry per flagged workload, ordered by its best-placed flag.

    `Ranking.flagged` is ordered by flag, so a workload appears as often as it
    has flagged metrics. Its position is the first place it occupies — taking the
    last would push a workload down the list for having *more* evidence against
    it, which is backwards.
    """
    grouped: dict[str, list[Flag]] = {}
    for item in ranking.flagged:
        grouped.setdefault(item.workload_id, []).append(item)
    return [(workload, tuple(flags)) for workload, flags in grouped.items()]
