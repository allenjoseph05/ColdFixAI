"""The prompt shape prompt caching can actually hit, and two ways it silently cannot.

Epic 5, S-5.7. `04-cost.md` §12.2 makes this the single largest cost variable in
the system: the investigate loop at 120 calls costs ~$39 uncached at 60k context
and ~$1.68 pruned and cached — **23x from one variable**. §4 turns that into an
architectural rule rather than an optimisation, and `CLAUDE.md` carries it as a
non-negotiable: the experiment log is append-only, and reordering or
re-summarizing it mid-investigation multiplies cost.

Caching is a **prefix match**: any byte change anywhere in the prefix invalidates
everything at or after it. Render order is `tools` → `system` → `messages`. So
AC 1's five segments are in the only order that works, and the four cacheable
boundaries fit exactly into the **four `cache_control` breakpoints** a request
allows — system, playbook, source, log — with the varying question after the last
of them, uncached by construction.

**AC 2 is structural here, not a discipline.** There is no method that reorders
the log, no method that re-summarizes it, and no method that changes a stable
segment. An `Investigation` captures its system prompt, playbook and source at
construction; `append` is the only way to add anything. A `datetime.now()` in the
system prompt is therefore evaluated once, which is what makes the prefix
byte-identical rather than merely intended to be.

**Two ways the cache silently stops working, both of which read as success.**
Neither raises: the request succeeds, `cache_read_input_tokens` is zero, and the
bill goes up.

*The minimum cacheable prefix is model-dependent and* **not monotonic**. A prefix
shorter than the model's minimum does not cache at all, and the newest models
have the smallest minimum while this project's cheap tier has the largest:

| Model | Minimum |
|---|---:|
| `claude-opus-5` | 512 |
| `claude-sonnet-5` | 1024 |
| `claude-haiku-4-5` | **4096** |

S-5.5 routes grounding's mechanical work to `claude-haiku-4-5` precisely because
it is cheap, and §12.3's engineered grounding is *ten calls with a mature
playbook* — a short prompt. Below 4096 tokens that prompt caches on the frontier
model and not on the cheap one, so **routing a step down a tier can raise its
effective cost**. That is the opposite of what the routing is for, and nothing in
the response says so.

*A breakpoint looks back at most* **20 content blocks** *for a prior entry.* An
experiment log rendered one block per experiment exceeds that at experiment 21 —
and S-5.4 caps investigation at **40 experiments**, so a log built the obvious way
stops caching exactly halfway to its own budget. The log is therefore rendered as
**one** growing block, which is also what makes *append-only* checkable: the log
at call N is a byte prefix of the log at call N+1.

**Token counts are measured, never estimated.** The viability check takes a count
from `messages.count_tokens` and answers *cannot tell* without one. `tiktoken` is
OpenAI's tokenizer and undercounts Claude by 15-20% on prose and far more on
code; an estimate presented as a measurement is the thing `CLAUDE.md` forbids,
and here it would be an estimate of whether a cost control is working.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from coldfix.cost.accounting import AccountingError, TokenUsage

# A request may carry at most four `cache_control` breakpoints. AC 1's structure
# has exactly four cacheable boundaries, which is not a coincidence — the fifth
# segment is the varying question, and caching it would write an entry no later
# call can read.
MAX_BREAKPOINTS = 4

# Each breakpoint walks back at most this many content blocks looking for a prior
# cache entry. Exceed it and the next request silently finds nothing.
LOOKBACK_BLOCKS = 20

# The minimum cacheable prefix, per model. **Not monotonic**: the newest models
# have the smallest minimum and this project's cheap tier has the largest. A
# prefix below the figure here does not cache at all, with no error — the
# response simply reports `cache_creation_input_tokens: 0`.
MINIMUM_CACHEABLE_PREFIX: Mapping[str, int] = {
    "claude-opus-5": 512,
    "claude-opus-5/fast": 512,
    "claude-fable-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-haiku-4-5": 4096,
}


class ContextError(AccountingError):
    """A prompt could not be assembled, or could not be assembled cacheably."""


class Segment(StrEnum):
    """AC 1's five parts, in render order. The order is the whole technique."""

    SYSTEM = "system prefix"
    PLAYBOOK = "playbook"
    SOURCE = "source under study"
    LOG = "experiment log"
    QUESTION = "current question"

    @property
    def cacheable(self) -> bool:
        """Everything but the question. Caching the question would write an entry
        no later call can read, and spend a breakpoint doing it."""
        return self is not Segment.QUESTION


@dataclass(frozen=True)
class Block:
    """One content block, and whether a breakpoint sits at its end."""

    segment: Segment
    text: str
    breakpoint: bool


class Cacheability(StrEnum):
    """Whether this prompt can hit the cache at all."""

    CACHEABLE = "the prefix clears this model's minimum"
    BELOW_MINIMUM = "the prefix is shorter than this model's minimum, so nothing will cache"
    UNKNOWN = "no measured token count was supplied, so this cannot be answered"


@dataclass(frozen=True)
class Viability:
    """The answer to *will this actually cache*, with its reason.

    `UNKNOWN` is a real answer rather than an omission. The check needs a token
    count from `messages.count_tokens`, and guessing one would be estimating
    whether a cost control works — S-4.5's rule that *could not tell* must stay
    distinct from *nothing wrong*.
    """

    verdict: Cacheability
    model: str
    minimum: int
    measured_tokens: int | None

    def describe(self) -> str:
        if self.verdict is Cacheability.UNKNOWN:
            return (
                f"Cacheability of the {self.model} prefix is unknown: no measured token count was "
                "supplied. Call `messages.count_tokens` — never `tiktoken`, which is OpenAI's "
                "tokenizer and undercounts Claude by 15-20% on prose and more on code"
            )
        if self.verdict is Cacheability.BELOW_MINIMUM:
            return (
                f"The {self.model} prefix is {self.measured_tokens} tokens, below its "
                f"{self.minimum}-token minimum, so **nothing will cache** — the request will "
                "succeed and report zero cache reads. Note the minimum is not monotonic across "
                "models: the cheap tier's is the largest, so routing this step down a tier can "
                "raise its effective cost"
            )
        return (
            f"The {self.model} prefix is {self.measured_tokens} tokens, clearing its "
            f"{self.minimum}-token minimum"
        )


def minimum_prefix(model: str) -> int:
    """This model's minimum cacheable prefix.

    Raises:
        ContextError: no minimum is recorded, so whether a prefix caches is not
            known — and a default would be a guess about a silent failure.
    """
    try:
        return MINIMUM_CACHEABLE_PREFIX[model]
    except KeyError:
        known = ", ".join(sorted(MINIMUM_CACHEABLE_PREFIX))
        message = (
            f"no minimum cacheable prefix is recorded for {model!r}, so whether its prompts cache "
            f"is not known. Recorded: {known}. The failure this guards is silent — a prefix below "
            "the minimum caches nothing and reports no error — so a default would hide it"
        )
        raise ContextError(message) from None


@dataclass
class Investigation:
    """One investigation's prompt, assembled so the cache can hit it.

    The stable segments are captured here and there is **no method that changes
    them**. That is what makes the prefix byte-identical rather than merely
    intended to be: a `datetime.now()` in the system prompt is evaluated once, at
    construction, instead of on every render.

    `append` is the only way to add to the log. There is no `reorder`, no
    `summarize`, no `replace` — `CLAUDE.md`'s append-only rule expressed as an
    absence rather than as a warning.
    """

    system: str
    playbook: str
    source: str
    model: str
    _log: list[str] = field(default_factory=list, repr=False)
    _usage: list[TokenUsage] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        minimum_prefix(self.model)
        blank = sorted(
            name
            for name, value in (
                ("system", self.system),
                ("playbook", self.playbook),
                ("source", self.source),
            )
            if not value.strip()
        )
        if blank:
            message = (
                f"these stable segments are empty: {blank}. A segment with nothing in it is a "
                "breakpoint spent on nothing, and there are only four"
            )
            raise ContextError(message)

    def append(self, entry: str) -> None:
        """Add one experiment to the log. The only way the prompt ever grows.

        Raises:
            ContextError: an empty entry, which would change the log's bytes
                without recording anything.
        """
        if not entry.strip():
            message = "an empty log entry invalidates the cached suffix and records nothing"
            raise ContextError(message)
        self._log.append(entry)

    @property
    def entries(self) -> Sequence[str]:
        return tuple(self._log)

    def log_text(self) -> str:
        """The whole log as **one** block.

        One rather than one-per-experiment, and that is the difference between
        caching and not: a breakpoint looks back at most 20 content blocks, and
        S-5.4 caps investigation at 40 experiments — so a log rendered per
        experiment stops caching at experiment 21, exactly halfway to its own
        budget, with no error.
        """
        return "\n".join(self._log)

    def stable_prefix(self) -> str:
        """Everything that must be byte-identical between consecutive calls.

        The log is excluded because it grows. It does not have to be identical to
        cache — it has to be **append-only**, so that everything before the last
        write is still a matching prefix.
        """
        return f"{self.system}\n{self.playbook}\n{self.source}"

    def render(self, question: str) -> tuple[Block, ...]:
        """The blocks to send, with breakpoints on the four cacheable segments.

        Raises:
            ContextError: an empty question, or more blocks than a breakpoint can
                look back over.
        """
        if not question.strip():
            message = "a prompt with no question asks the model nothing"
            raise ContextError(message)

        blocks = (
            Block(Segment.SYSTEM, self.system, breakpoint=True),
            Block(Segment.PLAYBOOK, self.playbook, breakpoint=True),
            Block(Segment.SOURCE, self.source, breakpoint=True),
            Block(Segment.LOG, self.log_text(), breakpoint=True),
            Block(Segment.QUESTION, question, breakpoint=False),
        )

        check_blocks(blocks)
        return blocks

    def viability(self, measured_prefix_tokens: int | None = None) -> Viability:
        """Whether this prompt will cache at all on this model.

        `measured_prefix_tokens` comes from `messages.count_tokens`. Without one
        the answer is `UNKNOWN` rather than a guess.
        """
        minimum = minimum_prefix(self.model)
        if measured_prefix_tokens is None:
            verdict = Cacheability.UNKNOWN
        elif measured_prefix_tokens < minimum:
            verdict = Cacheability.BELOW_MINIMUM
        else:
            verdict = Cacheability.CACHEABLE
        return Viability(
            verdict=verdict,
            model=self.model,
            minimum=minimum,
            measured_tokens=measured_prefix_tokens,
        )

    def record(self, usage: TokenUsage) -> None:
        """Note what one call in this investigation actually used. AC 4."""
        self._usage.append(usage)

    def hit_rate(self) -> Decimal | None:
        """Cache reads as a share of every prompt token this investigation sent.

        `None` before anything has been recorded. Includes the first call, which
        can never hit — see `report`, which separates it rather than letting it
        quietly drag the figure down.
        """
        prompt = sum(usage.prompt_tokens for usage in self._usage)
        if prompt == 0:
            return None
        read = sum(usage.cache_read_input_tokens for usage in self._usage)
        return Decimal(read) / Decimal(prompt)

    def warm_hit_rate(self) -> Decimal | None:
        """The same, excluding the first call. `None` below two calls.

        The first call of an investigation cannot hit — there is nothing to hit
        yet — and it pays the 1.25x write premium on top. Reporting only the
        blended figure would make a perfectly working cache look worse the
        shorter the investigation, which is backwards.
        """
        warm = self._usage[1:]
        prompt = sum(usage.prompt_tokens for usage in warm)
        if prompt == 0:
            return None
        read = sum(usage.cache_read_input_tokens for usage in warm)
        return Decimal(read) / Decimal(prompt)

    def report(self) -> str:
        """AC 4: the hit rate, measured and reported."""
        if not self._usage:
            return "Cache: no calls recorded."

        blended = self.hit_rate()
        warm = self.warm_hit_rate()
        if blended is None:
            return "Cache: calls recorded, but no prompt tokens."
        lines = [
            f"Cache over {len(self._usage)} calls: {blended:.0%} of prompt tokens read from cache"
        ]
        if warm is not None:
            lines.append(f"  after the first call: {warm:.0%} (the first can never hit)")
        else:
            lines.append("  one call only, which can never hit — it writes the cache for the next")

        written = sum(usage.cache_creation_input_tokens for usage in self._usage)
        if written:
            lines.append(f"  {written} tokens written to cache, billed above the uncached rate")
        return "\n".join(lines)


def check_blocks(blocks: Sequence[Block]) -> None:
    """Refuse a block sequence the cache cannot follow.

    Separate from `render` so it is reachable: `render` builds a fixed
    five-block tuple, so neither limit can fire from there and a guard no test
    can reach is a guard nobody has checked (S-3.12's finding). Both limits are
    real and both are silent when crossed — the request succeeds and the cache
    simply never hits.

    Raises:
        ContextError: more breakpoints than a request allows, or more blocks
            than a breakpoint looks back over.
    """
    breakpoints = sum(1 for block in blocks if block.breakpoint)
    if breakpoints > MAX_BREAKPOINTS:
        message = (
            f"{breakpoints} breakpoints, and a request allows {MAX_BREAKPOINTS}. The extra ones "
            "are not an error the API reports — they are simply not applied"
        )
        raise ContextError(message)
    if len(blocks) > LOOKBACK_BLOCKS:
        message = (
            f"{len(blocks)} content blocks, and a breakpoint looks back over at most "
            f"{LOOKBACK_BLOCKS}. Past that the next request finds no prior entry and silently "
            "pays full price — which is what a log rendered one block per experiment does at "
            "experiment 21, halfway to S-5.4's cap of 40"
        )
        raise ContextError(message)


def is_append_only(earlier: Sequence[str], later: Sequence[str]) -> bool:
    """Whether `later` only added to `earlier`.

    The checkable form of `CLAUDE.md`'s rule. Reordering, editing or
    re-summarizing all fail this, and all three look identical from the bill:
    every call after the change pays full input price instead of the 0.1x read
    rate.
    """
    return len(later) >= len(earlier) and list(later[: len(earlier)]) == list(earlier)
