"""Summaries in context, detail on demand — and the arithmetic that says whether it worked.

Epic 5, S-5.8. `04-cost.md` §5: the experiment log grows, but the agent does not
need forty full stdout dumps preloaded. It needs to know experiment 7 happened
and what it concluded, and to be able to ask for the rest.

    experiment 7 — ablation of get_discount_price
      → 8.24s becomes 1.11s. 87% of cost localized.

**Nothing is discarded — and that is structural, not a promise.** AC 4 is the
whole difference between this and naive truncation, so there is no method on
`PrunedLog` that drops, truncates or rewrites a record. `append` adds, `read`
returns, `render` shows less than it holds. The detail is *in* the log the whole
time; pruning is a rendering decision, not a storage one.

**The retrieved detail must never enter the cached prefix.** S-5.7 built a prompt
whose log is append-only precisely so the prefix stays byte-identical, and
inserting experiment 3's stack traces back into the middle of that log at call
fifty would invalidate every cached breakpoint after it — turning the 23x win
into a loss on the same call that was trying to save tokens. So `read` returns
text and changes nothing: the caller places it after the log, where a tool result
belongs.

**Retrieval is not free, and the report says so.** Pruning removes tokens from
every subsequent call; retrieval adds them back to the calls that ask. An agent
that retrieves everything nets to worse than no pruning at all, because it has
also paid for the round trips. §5 claims context drops 60-80%, and that is a
claim this module measures rather than repeats — `reduction` against what an
unpruned log would have carried, and `net_reduction` after what was actually
pulled back.

**The summary is composed, not authored.** `08-audit.md` F6 again: an agent asked
to summarize its own experiment can write *experiment 7 — nothing of interest*,
and the detail is then never retrieved by anyone. The header line is assembled by
the harness from the primitive and the target, so the one part a caller supplies
is the outcome — the thing only the measurement knows.

**AC 1 says one line and §5 shows two.** The binding constraint is neither: it is
the token budget §12.3 assumes, which is 12k for the whole pruned prompt. So each
*part* is required to be a single line and is bounded, giving §5's exact
two-line shape with a size that stays predictable across forty experiments.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from coldfix.cost.accounting import AccountingError

# AC 3: the prompt must say the detail is retrievable, or the agent will not ask.
# Rendered at the head of the log block, where it is stable — it never changes,
# so entries appended after it leave it a byte-identical prefix (S-5.7's rule).
RETRIEVAL_NOTICE = (
    "Experiment log. Each entry is a summary; the full output, stack traces, "
    "per-call timings and raw counters are retained and retrievable with "
    "read_experiment(n). Nothing here has been discarded — only deferred."
)

# Each part of a summary is one line and no longer than this. Forty experiments
# at two parts apiece is the log's whole contribution to §12.3's 12k prompt, and
# an unbounded part makes that number unpredictable rather than merely large.
MAX_SUMMARY_CHARS = 200

# §5's claim, which this module measures rather than repeats.
CLAIMED_REDUCTION = Decimal("0.60")


class PruningError(AccountingError):
    """A record could not be logged, or a retrieval could not be answered."""


@dataclass(frozen=True)
class ExperimentRecord:
    """One experiment: what it did, what it concluded, and everything it produced.

    `detail` is the full output — stdout, stacks, per-call timings, raw counters.
    It is held here always. Whether it is *rendered* is a separate question, and
    the only one pruning answers.
    """

    index: int
    primitive: str
    target: str
    outcome: str
    detail: str

    def summary(self) -> str:
        """§5's shape, composed by the harness rather than written by an agent.

        The header comes from the primitive and the target, which are facts about
        what ran. Only the outcome is supplied, because only the measurement
        knows it — and F6's finding is that everything an agent can author about
        its own success, it will author favourably.
        """
        return f"experiment {self.index} — {self.primitive} of {self.target}\n  → {self.outcome}"


@dataclass
class PrunedLog:
    """Every experiment, rendered short. **Nothing here can drop a record.**

    There is no `truncate`, no `summarize`, no `forget`, no `evict` — AC 4 as an
    absence, the same construction S-5.7 used for the append-only rule. Pruning
    is what `render` leaves out, never what the log throws away.
    """

    _records: list[ExperimentRecord] = field(default_factory=list, repr=False)
    _retrieved: list[int] = field(default_factory=list, repr=False)

    def append(self, primitive: str, target: str, outcome: str, detail: str) -> ExperimentRecord:
        """Record one experiment and return it, with its index assigned here.

        The index is assigned rather than supplied because `read_experiment(7)`
        has to mean the seventh experiment. A caller-supplied index can collide,
        skip, or restart, and each of those makes a retrieval return somebody
        else's measurement without any error.

        Raises:
            PruningError: a summary part is empty, multi-line, or over the
                length the prompt budget assumes.
        """
        parts = {"primitive": primitive, "target": target, "outcome": outcome}
        for name, value in parts.items():
            if not value.strip():
                message = f"an experiment with no {name} cannot be summarized usefully"
                raise PruningError(message)
            if "\n" in value:
                message = (
                    f"the {name} spans multiple lines, and a summary that grows with its subject "
                    "is the thing pruning exists to prevent"
                )
                raise PruningError(message)
            if len(value) > MAX_SUMMARY_CHARS:
                message = (
                    f"the {name} is {len(value)} characters and the limit is {MAX_SUMMARY_CHARS}. "
                    "Forty experiments is the log's whole share of the 12k prompt `04-cost.md` "
                    "§12.3 assumes; put the rest in the detail, where it is still retrievable"
                )
                raise PruningError(message)

        record = ExperimentRecord(
            index=len(self._records) + 1,
            primitive=primitive,
            target=target,
            outcome=outcome,
            detail=detail,
        )
        self._records.append(record)
        return record

    @property
    def records(self) -> Sequence[ExperimentRecord]:
        return tuple(self._records)

    def render(self) -> str:
        """What goes into the prompt: the notice, then one summary per experiment.

        Feeds S-5.7's log segment. Append-only by construction — the notice never
        changes and summaries only accumulate — so the rendered text at call N is
        a byte prefix of the text at call N+1.
        """
        summaries = "\n".join(record.summary() for record in self._records)
        return f"{RETRIEVAL_NOTICE}\n\n{summaries}" if summaries else RETRIEVAL_NOTICE

    def read_experiment(self, index: int) -> str:
        """AC 2: the full output, stacks and raw counters for one experiment.

        **Returns text and changes nothing.** The caller places it after the log,
        where a tool result belongs — writing it back into the log would insert
        content into the middle of a cached prefix and invalidate every
        breakpoint after it, which is a larger loss than the tokens it saved.

        Raises:
            PruningError: no experiment has that index. Named rather than
                silent, because the caller asking is a model and a model that
                guesses an index must be told, not handed the nearest record.
        """
        if not 1 <= index <= len(self._records):
            available = f"1-{len(self._records)}" if self._records else "none yet"
            message = (
                f"there is no experiment {index}; this investigation has run {available}. A "
                "retrieval that quietly returned the nearest record would answer a question "
                "nobody asked with a measurement of something else"
            )
            raise PruningError(message)

        self._retrieved.append(index)
        return self._records[index - 1].detail

    @property
    def retrievals(self) -> Sequence[int]:
        return tuple(self._retrieved)

    @property
    def deferred_chars(self) -> int:
        """Everything held back from the prompt."""
        return sum(len(record.detail) for record in self._records)

    @property
    def rendered_chars(self) -> int:
        return len(self.render())

    @property
    def retrieved_chars(self) -> int:
        """What was pulled back in, counting a second retrieval twice.

        Twice on purpose: the agent paid for it twice. A distinct-experiments
        count would flatter the number in exactly the case worth catching, which
        is a loop re-reading the same experiment.
        """
        return sum(len(self._records[index - 1].detail) for index in self._retrieved)

    def reduction(self) -> Decimal | None:
        """How much smaller the prompt is than an unpruned log. `None` when empty.

        §5 claims 60-80%. Measured rather than repeated, because a log of forty
        one-line experiments with short details would not reach it, and a
        technique that is not delivering should say so rather than be assumed.
        """
        if not self._records:
            return None
        unpruned = self.rendered_chars + self.deferred_chars
        return Decimal(self.deferred_chars) / Decimal(unpruned)

    def net_reduction(self) -> Decimal | None:
        """The same, after what retrieval added back. `None` when empty.

        The honest figure. Pruning removes tokens from every later call and
        retrieval adds them to the calls that ask, so an agent retrieving
        everything nets to worse than no pruning — it also paid for the round
        trips. Retrieving every experiment **once** cancels the saving exactly —
        the technique nets to zero on tokens and to a loss on round trips — and
        any re-read takes it negative. Not clamped, because a clamp would hide
        precisely the case the metric exists to expose.
        """
        if not self._records:
            return None
        unpruned = self.rendered_chars + self.deferred_chars
        return Decimal(self.deferred_chars - self.retrieved_chars) / Decimal(unpruned)

    def meets_claim(self) -> bool:
        """Whether the measured net reduction reaches §5's 60%."""
        net = self.net_reduction()
        return net is not None and net >= CLAIMED_REDUCTION

    def report(self) -> str:
        """What pruning bought, for the run report."""
        if not self._records:
            return "Pruning: no experiments logged."

        reduction = self.reduction()
        net = self.net_reduction()
        lines = [
            f"Pruning over {len(self._records)} experiments: "
            f"{self.rendered_chars} characters in context, {self.deferred_chars} deferred "
            f"({reduction:.0%} smaller than an unpruned log)"
        ]
        if self._retrieved:
            lines.append(
                f"  {len(self._retrieved)} retrievals pulled back {self.retrieved_chars} "
                f"characters — net {net:.0%}"
            )
        else:
            lines.append("  nothing retrieved, so the full reduction stands")
        if not self.meets_claim():
            lines.append(
                f"  below the {CLAIMED_REDUCTION:.0%} `04-cost.md` §5 claims — the detail is "
                "small relative to the summaries, or retrieval is undoing the saving"
            )
        return "\n".join(lines)
