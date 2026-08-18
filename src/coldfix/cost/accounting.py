"""Token accounting: what every model call used, what it cost, and per what.

Epic 5, S-5.3. The acceptance criteria ask for eight fields per call, cost
queryable three ways, and euros per confirmed finding. Two of those three are
arithmetic. The interesting part is that the field list, read literally, cannot
produce a cost — and finding that out is most of this story.

**"Cached tokens" is two numbers with opposite signs.** The API reports
`cache_creation_input_tokens` and `cache_read_input_tokens` separately, and they
bill in opposite directions: a cache **write** costs *more* than an uncached
token — 1.25x at the five-minute TTL, 2x at the hour — while a cache **read**
costs 0.1x. A single `cached_tokens` figure is not a lossy summary of those two,
it is an unusable one: no arithmetic recovers a bill from it, and the error is
signed the flattering way, because the number people expect caching to produce is
a discount. So there is no `cached_tokens` field anywhere in this module.

**`input_tokens` is the uncached remainder, not the prompt.** The whole prompt is
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. Recorded
under the API's own names for that reason: a field called `input_tokens` sitting
beside a field called `cached_tokens` reads as *the prompt, of which some were
cached*, and a reader who adds them double-counts while a reader who does not
under-reports. `TokenUsage.prompt_tokens` is the sum, spelled out.

**Cost is computed here and cannot be set.** `CLAUDE.md` forbids an agent
reporting a measurement, and a cost is a measurement — of tokens, against a
price. So `ModelCall` has no cost field: it has usage, a model, and a property
that prices them. This is S-4.1's construction for `work_verified`, applied to
the number most worth misreporting.

**An unknown model is refused rather than priced.** Fast mode on `claude-opus-5`
bills at $10/$50 rather than $5/$25 — the same model id at twice the rate — so
the price book is keyed on the *billing* identity, and a default price for
anything unrecognised would produce a bill that is wrong by a factor nobody sees.

**Money is `Decimal`.** A run is ~250 calls and a project is ~1,000 runs, and
`04-cost.md` §12's figures are quoted to the cent. Binary floating point does not
represent a cent, and the error accumulates in a sum this long.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

# Rates are quoted per million tokens everywhere Anthropic publishes them, and
# the arithmetic reads like the published table when the divisor is named.
PER_MILLION = Decimal(1_000_000)

# `04-cost.md` §8: the Batch API is half price, at the cost of latency, and §8
# lists evaluation runs, agreement studies and ablations as the work that can
# take it. A multiplier rather than a separate price book, because it applies to
# every model equally and duplicating the table would let the two drift.
BATCH_MULTIPLIER = Decimal("0.5")

# Prompt-cache billing. A read is the discount everybody expects; a **write costs
# more than not caching at all**, which is the half that gets forgotten and the
# reason a breakpoint that is written and never read is a loss (ADR 002 records
# the same fact from the caching side).
CACHE_READ_MULTIPLIER = Decimal("0.1")
_CACHE_WRITE_MULTIPLIERS = {
    "5m": Decimal("1.25"),
    "1h": Decimal("2.0"),
}


class AccountingError(Exception):
    """A call could not be priced, or a total could not be reported."""


class UnknownModelError(AccountingError):
    """This model is not in the price book, so its cost is not known.

    Raised rather than defaulted. A default rate produces a bill that looks
    exactly like a real one and is wrong by whatever the difference happens to
    be — and the first model this system meets that is not in the book will be a
    newer, dearer one, so the error is signed the flattering way.
    """


class Phase(StrEnum):
    """Where in a run a call happened. `04-cost.md` §12.1's rows, verbatim.

    Screening has no member and that is deliberate: Epic 4 is *zero model calls*,
    asserted structurally, so a phase for it would be a slot that can only ever
    be filled by a bug.
    """

    GROUND = "ground"
    INVESTIGATE = "investigate"
    FINDING_AUDIT = "finding audit"
    TEST_AUDIT = "test audit"
    REPAIR = "repair"
    PATCH_AUDIT = "patch audit"


class Agent(StrEnum):
    """Which agent made the call. ADR 002's five."""

    EXPLORER = "explorer"
    DIAGNOSTICIAN = "diagnostician"
    SURGEON = "surgeon"
    FINDING_AUDITOR = "finding auditor"
    ADVERSARY = "adversary"


class StepClass(StrEnum):
    """What kind of thinking the step needs. S-5.5 routes on this.

    Recorded here rather than in S-5.5 because the routing story needs the
    measurement to argue with: `04-cost.md` §12.3 splits the investigate loop 15
    creative to 105 mechanical, and that ratio is a claim about a real run that
    nothing could check until calls were counted by class.
    """

    CREATIVE = "creative"
    """Hypothesis generation, attack design. Never cascades — no validator exists."""

    MECHANICAL = "mechanical"
    """A deterministic validator can check the answer."""


@dataclass(frozen=True)
class Price:
    """What one model charges, per million tokens, in US dollars.

    Dollars because that is what the vendor bills in. The euro figure the run
    report quotes is a conversion, and it carries the rate that produced it.
    """

    input_usd: Decimal
    output_usd: Decimal


# Rates published for the first-party API, recorded with the date they were read
# so a stale table is visible rather than merely wrong. ADR 002's indicative
# table matches for the three models it lists; it also says to re-check before
# publishing any cost figure, which is what this constant makes possible.
PRICE_BOOK_AS_OF = date(2026, 8, 9)

PRICE_BOOK: Mapping[str, Price] = {
    "claude-opus-5": Price(Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": Price(Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": Price(Decimal("1.00"), Decimal("5.00")),
    "claude-opus-4-8": Price(Decimal("5.00"), Decimal("25.00")),
    "claude-fable-5": Price(Decimal("10.00"), Decimal("50.00")),
    # Fast mode is the same model at twice the rate, so it is a separate entry
    # rather than a flag. A price book keyed on the model id alone would bill a
    # fast-mode run at half what it costs, and nothing in the response says which
    # was used except `usage.speed`.
    "claude-opus-5/fast": Price(Decimal("10.00"), Decimal("50.00")),
}


@dataclass(frozen=True)
class TokenUsage:
    """What one call used, under the API's own field names.

    Renaming any of these would be the whole defect this module exists to avoid.
    `input_tokens` is what the API reports: the part of the prompt that was
    neither written to nor read from the cache.
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_ttl: str = "5m"
    """Which write multiplier applies — 1.25x at five minutes, 2x at an hour.

    Recorded per call rather than assumed, because the two differ by 60% on the
    write and a run that used the hour TTL priced at the five-minute rate is
    under-billed by that much on every cached prefix.
    """

    def __post_init__(self) -> None:
        negative = sorted(
            name
            for name, value in (
                ("input_tokens", self.input_tokens),
                ("output_tokens", self.output_tokens),
                ("cache_creation_input_tokens", self.cache_creation_input_tokens),
                ("cache_read_input_tokens", self.cache_read_input_tokens),
            )
            if value < 0
        )
        if negative:
            message = f"these token counts are negative and cannot be: {negative}"
            raise AccountingError(message)
        if self.cache_ttl not in _CACHE_WRITE_MULTIPLIERS:
            known = ", ".join(sorted(_CACHE_WRITE_MULTIPLIERS))
            message = (
                f"{self.cache_ttl!r} is not a cache TTL this bills for; known: {known}. A write "
                "multiplier guessed from an unrecognised TTL would misprice every cached prefix "
                "in the run"
            )
            raise AccountingError(message)

    @property
    def prompt_tokens(self) -> int:
        """The whole prompt: uncached remainder plus both cache kinds.

        Spelled out because `input_tokens` alone is the number a reader assumes
        this is. An agent that ran for an hour and reports 4k `input_tokens` did
        not process 4k tokens — the rest was served from cache.
        """
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def cache_hit_rate(self) -> float | None:
        """Share of the prompt served from cache. `None` on an empty prompt.

        S-5.7 measures and reports this; it is computable from what is already
        recorded here, so recording it separately would be storing a number that
        can disagree with its own inputs.
        """
        if self.prompt_tokens == 0:
            return None
        return self.cache_read_input_tokens / self.prompt_tokens


@dataclass(frozen=True)
class ModelCall:
    """One model call: AC 1's fields, with cost computed rather than supplied.

    **There is no cost field and no way to set one.** Cost is tokens priced
    against a book, so an agent that could write it could report a measurement —
    which `CLAUDE.md` forbids, and which S-4.1 already closed once for
    `work_verified`. It has usage, a model, and a property.
    """

    phase: Phase
    agent: Agent
    step_class: StepClass
    model: str
    usage: TokenUsage
    at: datetime
    finding_id: str | None = None
    """Which finding this call was spent on, where one exists yet.

    `None` is the common case rather than an omission: grounding runs once per
    repository before any finding is known, and `04-cost.md` §11 makes that
    sharing deliberate. A ledger that demanded a finding here would have to
    invent one, and the invented one would collect the whole grounding bill.
    """

    batched: bool = False
    """Submitted through the Batch API, which `04-cost.md` §8 prices at half."""

    @property
    def cost_usd(self) -> Decimal:
        """What this call cost, in dollars.

        Four token kinds, four rates. The two cache kinds are priced apart
        because they bill apart — this is the arithmetic a single `cached_tokens`
        field makes impossible.

        Raises:
            UnknownModelError: the model is not in the price book.
        """
        price = price_of(self.model)
        write_multiplier = _CACHE_WRITE_MULTIPLIERS[self.usage.cache_ttl]

        billed = (
            self.usage.input_tokens * price.input_usd
            + self.usage.cache_creation_input_tokens * price.input_usd * write_multiplier
            + self.usage.cache_read_input_tokens * price.input_usd * CACHE_READ_MULTIPLIER
            + self.usage.output_tokens * price.output_usd
        ) / PER_MILLION

        return billed * BATCH_MULTIPLIER if self.batched else billed


def price_of(model: str) -> Price:
    """The published rate for a model, refusing one the book does not list.

    Raises:
        UnknownModelError: no rate is recorded for this model.
    """
    try:
        return PRICE_BOOK[model]
    except KeyError:
        known = ", ".join(sorted(PRICE_BOOK))
        message = (
            f"no price is recorded for {model!r}, so what it cost is not known. The book was read "
            f"on {PRICE_BOOK_AS_OF.isoformat()} and lists: {known}. A default rate would produce a "
            "bill indistinguishable from a real one and wrong by whatever the difference is"
        )
        raise UnknownModelError(message) from None


@dataclass(frozen=True)
class ExchangeRate:
    """Euros per dollar, and the day that was true.

    AC 3 asks for euros; the vendor bills in dollars. The rate is therefore an
    input to the report and not a constant in this file — a hardcoded rate is
    correct on the day it is written and silently wrong every day after, and a
    cost figure is exactly the kind of number that gets quoted a year later.

    The date travels with the number for the reason every other precondition in
    this project does: *€2,150* means one thing with a rate beside it and
    something much weaker without.
    """

    euros_per_dollar: Decimal
    as_of: date

    def __post_init__(self) -> None:
        if self.euros_per_dollar <= 0:
            message = f"an exchange rate must be positive, got {self.euros_per_dollar}"
            raise AccountingError(message)

    def convert(self, usd: Decimal) -> Decimal:
        return usd * self.euros_per_dollar

    def describe(self) -> str:
        return f"€{self.euros_per_dollar} per $1, as of {self.as_of.isoformat()}"


@dataclass
class Ledger:
    """Every model call in one run, in the order they were made.

    Append-only, like the experiment log and for a weaker version of the same
    reason: a total assembled from a list somebody edited is a total nobody can
    check against the vendor's invoice.
    """

    calls: list[ModelCall] = field(default_factory=list)

    def record(self, call: ModelCall) -> None:
        self.calls.append(call)

    @property
    def total_usd(self) -> Decimal:
        return sum((call.cost_usd for call in self.calls), Decimal(0))

    def by_phase(self) -> Mapping[Phase, Decimal]:
        """AC 2's first cut. Every phase that ran, whether or not it cost much."""
        totals: dict[Phase, Decimal] = {}
        for call in self.calls:
            totals[call.phase] = totals.get(call.phase, Decimal(0)) + call.cost_usd
        return totals

    def by_finding(self) -> Mapping[str, Decimal]:
        """AC 2's second cut, covering only the calls attributed to a finding.

        Deliberately **not** the whole run. `unattributed_usd` holds the rest,
        and the two sum to the total — see `reconciles`, which is the assertion
        that keeps a per-finding table from quietly costing less than the run.
        """
        totals: dict[str, Decimal] = {}
        for call in self.calls:
            if call.finding_id is None:
                continue
            totals[call.finding_id] = totals.get(call.finding_id, Decimal(0)) + call.cost_usd
        return totals

    @property
    def unattributed_usd(self) -> Decimal:
        """What was spent before any finding existed — grounding, mostly.

        `04-cost.md` §11: grounding happens once per repository, not once per
        finding. Splitting it across findings would make each finding look dearer
        than it was and would make a one-finding run look like grounding is free.
        """
        return sum(
            (call.cost_usd for call in self.calls if call.finding_id is None),
            Decimal(0),
        )

    @property
    def reconciles(self) -> bool:
        """Whether the per-finding costs and the unattributed remainder sum to the total.

        Trivially true today and worth asserting anyway: the first time a call is
        attributed two ways, or a phase is excluded from one of the cuts, the
        per-finding table starts costing less than the run and nothing else
        notices. A number that is quoted has to be reconcilable against the one
        it came from.
        """
        return sum(self.by_finding().values(), Decimal(0)) + self.unattributed_usd == self.total_usd

    def usage(self) -> TokenUsage:
        """Every token this run used, added up.

        The TTL of the sum is the one every call shared, or the five-minute
        default where they differed — the aggregate is for reporting volume, and
        `cost_usd` is never taken from it. Costs come from calls, priced one at a
        time against their own model and their own TTL.
        """
        ttls = {call.usage.cache_ttl for call in self.calls}
        return TokenUsage(
            input_tokens=sum(call.usage.input_tokens for call in self.calls),
            output_tokens=sum(call.usage.output_tokens for call in self.calls),
            cache_creation_input_tokens=sum(
                call.usage.cache_creation_input_tokens for call in self.calls
            ),
            cache_read_input_tokens=sum(call.usage.cache_read_input_tokens for call in self.calls),
            cache_ttl=ttls.pop() if len(ttls) == 1 else "5m",
        )


@dataclass(frozen=True)
class RunReport:
    """What a run cost, and what it cost per confirmed finding.

    `confirmed` is the count E9's finding audit let through, supplied by the
    caller rather than counted here: this module knows what was spent, not what
    survived an audit, and a cost ledger that decided which findings counted
    would be grading its own denominator.
    """

    ledger: Ledger
    confirmed_findings: int
    rate: ExchangeRate

    def __post_init__(self) -> None:
        if self.confirmed_findings < 0:
            message = f"a run cannot confirm {self.confirmed_findings} findings"
            raise AccountingError(message)

    @property
    def total_eur(self) -> Decimal:
        return self.rate.convert(self.ledger.total_usd)

    @property
    def eur_per_confirmed_finding(self) -> Decimal | None:
        """AC 3. `None` when the run confirmed nothing.

        Not zero, not the run total, and not infinity. **A run that confirms
        nothing is a successful run** — S-4.5 ships "screened nine workloads,
        nothing found" as an answer — so the cost is real and the ratio is
        undefined, which is a different statement from *it cost nothing* and from
        *it cost everything*. The same rule S-4.2 applies to a metric that starts
        at zero: `None`, because dividing by it would put a made-up number where
        a fact should be.
        """
        if self.confirmed_findings == 0:
            return None
        return self.total_eur / self.confirmed_findings

    def render(self) -> str:
        """The run report's cost section, in the form a human reads.

        Carries the exchange rate and the price book's date, because a euro
        figure without them is a number whose meaning expires silently.
        """
        lines = [
            f"Run cost: €{_cents(self.total_eur)} (${_cents(self.ledger.total_usd)})",
            f"  rate: {self.rate.describe()}; prices read {PRICE_BOOK_AS_OF.isoformat()}",
        ]

        per_finding = self.eur_per_confirmed_finding
        if per_finding is None:
            lines.append(
                "  euros per confirmed finding: not applicable — this run confirmed no findings. "
                "The run still cost what it cost; a null result is an answer, not a failure."
            )
        else:
            lines.append(
                f"  euros per confirmed finding: €{_cents(per_finding)} "
                f"over {self.confirmed_findings} confirmed"
            )

        usage = self.ledger.usage()
        hit_rate = usage.cache_hit_rate
        measured = "no prompt tokens" if hit_rate is None else f"{hit_rate:.0%}"
        lines.append(
            f"  {len(self.ledger.calls)} calls, {usage.prompt_tokens} prompt tokens "
            f"({usage.cache_read_input_tokens} read from cache, {measured}), "
            f"{usage.output_tokens} output"
        )

        for phase, spent in sorted(self.ledger.by_phase().items()):
            lines.append(f"  {phase.value}: €{_cents(self.rate.convert(spent))}")

        unattributed = self.ledger.unattributed_usd
        if unattributed:
            lines.append(
                f"  not attributed to any finding: €{_cents(self.rate.convert(unattributed))} "
                "(grounding is shared across a repository, not split between findings)"
            )
        return "\n".join(lines)


def _cents(amount: Decimal) -> str:
    return f"{amount:.2f}"


def total_of(calls: Sequence[ModelCall]) -> Decimal:
    """What a sequence of calls cost, for a caller that has no ledger yet."""
    return sum((call.cost_usd for call in calls), Decimal(0))
