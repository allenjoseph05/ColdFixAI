"""What a vendor actually costs on this workload, which is not its list price.

Epic 5, S-5.9 — **partial**. AC 3 and AC 5 are here; AC 1, 2, 4 and 6 need a
second vendor's account and E9's finding audit, and are recorded as blocked in
the backlog rather than faked. This module is the half that needs neither: the
cost model that makes ADR-002 falsifiable instead of re-argued from list prices
and memory.

**List price is the wrong number for this workload.** A cache read bills at 0.1x
of input, and S-5.7's whole design exists so the cached prefix stays
byte-identical — so effective cost is dominated by *hit rate*, not sticker rate.
At an 85% hit rate an input token costs roughly a quarter of its list price, and
a vendor 30% cheaper per token loses to one 30% dearer with better cache
semantics.

**Three things move effective cost independently of price**, which is why AC 5
asks for them recorded rather than assumed:

- the **minimum cacheable prefix**, below which nothing caches at all and the
  hit rate is zero however well the prompt is built (S-5.7: this is per *model*
  and not monotonic — Anthropic's cheap tier has the largest);
- the **cache TTL**, since a prefix that expires between calls is written twice
  and read never, and a write costs *more* than not caching (ADR 056);
- **prefix semantics** — caching that is not a prefix match cannot be exploited
  by an append-only log at all, which would make `CLAUDE.md`'s append-only rule
  buy nothing on that vendor.

**One profile is recorded, and the second column is left empty on purpose.**
Anthropic's figures are taken from the published documentation and derived from
the tables S-5.3 and S-5.7 already hold, so there is one source of truth rather
than a copy that drifts. No second vendor is recorded because no measurement of
one exists and none of its cache figures were in front of the author — inventing
them would be exactly the *re-argued from memory* failure this story was written
to end.

**The measured half is optional and reports its absence.** `MeasuredRun` carries
cost per confirmed finding, experiments to conclusion and the observed hit rate,
and every one of them is `None` until a real run supplies it. A comparison that
defaulted them to zero would read as *this vendor is free and instantaneous*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from coldfix.cost.accounting import PER_MILLION, PRICE_BOOK, AccountingError, Price, price_of
from coldfix.cost.context import MINIMUM_CACHEABLE_PREFIX

# A comparison of one is not a comparison. Named rather than inline, because the
# whole story is about not declaring a winner from a field of one.
MINIMUM_VENDORS_FOR_A_COMPARISON = 2


class VendorError(AccountingError):
    """A vendor's cost could not be modelled, or a comparison could not be made."""


@dataclass(frozen=True)
class CachePolicy:
    """The cache facts that move effective cost independently of list price.

    AC 5 asks for the minimum cacheable prefix and the TTL specifically. Both are
    recorded per vendor because both are silent when they bite: below the minimum
    nothing caches and no error says so, and a TTL shorter than the gap between
    calls turns every read into a second write.
    """

    read_multiplier: Decimal
    """What a cache read costs, as a share of the list input rate."""

    write_multipliers: Mapping[str, Decimal]
    """What a cache write costs, by TTL. **Above 1.0** — a write costs more than
    not caching, which is the half of prompt caching that gets forgotten."""

    minimum_cacheable_prefix: Mapping[str, int]
    """Per model, because on Anthropic it is per model and not monotonic."""

    prefix_match: bool
    """Whether caching is a prefix match at all.

    If it is not, an append-only log buys nothing — `CLAUDE.md`'s rule and
    S-5.7's whole assembly are exploiting this property specifically, so a vendor
    without it is not merely dearer, it is a different architecture.
    """

    scope: str
    """What a cache entry is keyed to. Model-scoped means switching model within
    a run discards the cache, which S-5.5's routing has to respect."""


@dataclass(frozen=True)
class VendorProfile:
    """Everything about a vendor that decides what this workload costs there.

    Recorded with a date and a source, because the whole point of the story is
    that the decision stops being re-argued from memory. A profile with no
    provenance is memory with a dataclass around it.
    """

    vendor: str
    prices: Mapping[str, Price]
    cache: CachePolicy
    recorded_on: date
    source: str

    def price_for(self, model: str) -> Price:
        try:
            return self.prices[model]
        except KeyError:
            known = ", ".join(sorted(self.prices))
            message = f"{self.vendor} has no recorded price for {model!r}; recorded: {known}"
            raise VendorError(message) from None

    def minimum_prefix(self, model: str) -> int:
        try:
            return self.cache.minimum_cacheable_prefix[model]
        except KeyError:
            message = (
                f"{self.vendor} has no recorded minimum cacheable prefix for {model!r}, so "
                "whether its prompts cache is not known — and below the minimum nothing caches "
                "with no error to say so"
            )
            raise VendorError(message) from None

    def write_multiplier(self, ttl: str) -> Decimal:
        try:
            return self.cache.write_multipliers[ttl]
        except KeyError:
            known = ", ".join(sorted(self.cache.write_multipliers))
            message = f"{self.vendor} does not offer a {ttl!r} cache TTL; it offers: {known}"
            raise VendorError(message) from None


# Anthropic's figures, derived from the tables S-5.3 and S-5.7 already hold
# rather than copied beside them — a second copy is a second thing to go stale,
# and going stale is the failure this story exists to prevent.
ANTHROPIC = VendorProfile(
    vendor="Anthropic",
    prices=dict(PRICE_BOOK),
    cache=CachePolicy(
        read_multiplier=Decimal("0.1"),
        write_multipliers={"5m": Decimal("1.25"), "1h": Decimal("2.0")},
        minimum_cacheable_prefix=dict(MINIMUM_CACHEABLE_PREFIX),
        prefix_match=True,
        scope="model",
    ),
    recorded_on=date(2026, 8, 9),
    source="platform.claude.com pricing and prompt-caching documentation",
)

# The only vendor recorded. A second entry is a data addition rather than a code
# change, and it is deliberately absent: no run against another vendor has
# happened and none of its cache figures were verified, so writing one would be
# the *re-argued from memory* failure this story was written to end.
VENDORS: Mapping[str, VendorProfile] = {ANTHROPIC.vendor: ANTHROPIC}


@dataclass(frozen=True)
class WorkloadShape:
    """The shape of the calls a run makes, which is what decides effective cost.

    Not a measurement of any particular run — a description of one, so the same
    shape can be priced against several vendors. `04-cost.md` §12.2's engineered
    investigate loop is the shape that matters: 120 calls, 12k prompt, 85% of it
    read from cache.
    """

    calls: int
    prompt_tokens: int
    output_tokens: int
    cached_share: Decimal
    """Share of prompt tokens served from cache. The dominant variable."""

    cache_ttl: str = "5m"

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.cached_share <= Decimal(1):
            message = f"a cached share must be between 0 and 1, got {self.cached_share}"
            raise VendorError(message)
        negative = sorted(
            name
            for name, value in (
                ("calls", self.calls),
                ("prompt_tokens", self.prompt_tokens),
                ("output_tokens", self.output_tokens),
            )
            if value < 0
        )
        if negative:
            message = f"these cannot be negative: {negative}"
            raise VendorError(message)


def caches_at_all(profile: VendorProfile, model: str, shape: WorkloadShape) -> bool:
    """Whether this prompt clears the vendor's minimum for this model.

    Checked before any effective-cost figure is believed: below the minimum the
    hit rate is zero however good the prompt is, and no error says so.
    """
    return shape.prompt_tokens >= profile.minimum_prefix(model)


def effective_input_usd_per_mtok(
    profile: VendorProfile, model: str, shape: WorkloadShape
) -> Decimal:
    """What an input token actually costs here, per million.

    AC 3's headline. The list rate is what a token costs uncached; this is what
    it costs given the hit rate, the write premium, and whether the prompt is
    long enough to cache at that vendor at all.

    A prompt below the minimum is priced at the full list rate, because that is
    what it will be billed — not at the rate the hit rate would suggest.
    """
    price = profile.price_for(model)
    if not caches_at_all(profile, model, shape):
        return price.input_usd

    read = shape.cached_share * profile.cache.read_multiplier
    # Every cached token is written once before it can be read. Over `calls`
    # calls it is written once and read for the rest, so the write is amortised
    # — and amortising it is the reason a one-call run never pays off.
    writes_per_token = Decimal(1) / Decimal(max(shape.calls, 1))
    write = shape.cached_share * writes_per_token * profile.write_multiplier(shape.cache_ttl)
    uncached = Decimal(1) - shape.cached_share
    return price.input_usd * (read + write + uncached)


def effective_run_usd(profile: VendorProfile, model: str, shape: WorkloadShape) -> Decimal:
    """What the whole shape costs at this vendor."""
    price = profile.price_for(model)
    per_mtok = effective_input_usd_per_mtok(profile, model, shape)
    inputs = Decimal(shape.calls * shape.prompt_tokens) * per_mtok / PER_MILLION
    outputs = Decimal(shape.calls * shape.output_tokens) * price.output_usd / PER_MILLION
    return inputs + outputs


def list_run_usd(profile: VendorProfile, model: str, shape: WorkloadShape) -> Decimal:
    """The same shape priced at sticker, for the comparison AC 3 asks for."""
    price = profile.price_for(model)
    inputs = Decimal(shape.calls * shape.prompt_tokens) * price.input_usd / PER_MILLION
    outputs = Decimal(shape.calls * shape.output_tokens) * price.output_usd / PER_MILLION
    return inputs + outputs


@dataclass(frozen=True)
class MeasuredRun:
    """What a real run against a vendor found. Every field optional, and absent by default.

    AC 2 and AC 4 live here, and both are `None` until a run supplies them. A
    comparison that defaulted them to zero would read as *this vendor is free and
    reaches its conclusions instantly*.

    **`experiments_to_conclusion` is not merely unmeasured — S-0.8 found it
    unmeasurable with the current scenario set.** In sixty runs the model chose
    *no finding, stop* zero times: it reasons correctly, withholds the verdict,
    and proposes one more experiment. Until something bounds that, the figure has
    no value to report, and S-5.4's budget halt is what bounds the damage.
    """

    vendor: str
    usd_per_confirmed_finding: Decimal | None = None
    experiments_to_conclusion: Decimal | None = None
    measured_cache_hit_rate: Decimal | None = None
    note: str = ""


@dataclass(frozen=True)
class Comparison:
    """One workload shape priced across the vendors recorded, with what is missing named."""

    shape: WorkloadShape
    model: str
    profiles: Sequence[VendorProfile]
    measured: Mapping[str, MeasuredRun] = field(default_factory=dict)

    def effective(self) -> Mapping[str, Decimal]:
        return {
            profile.vendor: effective_run_usd(profile, self.model, self.shape)
            for profile in self.profiles
        }

    def cheapest(self) -> str | None:
        """The vendor with the lowest effective cost. `None` below two vendors.

        A comparison of one is not a comparison, and reporting a winner from a
        field of one is how ADR-002 came to be defended rather than tested.
        """
        if len(self.profiles) < MINIMUM_VENDORS_FOR_A_COMPARISON:
            return None
        return min(self.effective().items(), key=lambda item: item[1])[0]

    def render(self) -> str:
        lines = [
            f"Effective cost of {self.shape.calls} calls x {self.shape.prompt_tokens} prompt "
            f"tokens at {self.shape.cached_share:.0%} cached, on {self.model}:"
        ]
        for profile in self.profiles:
            effective = effective_run_usd(profile, self.model, self.shape)
            sticker = list_run_usd(profile, self.model, self.shape)
            caching = (
                f"caches above {profile.minimum_prefix(self.model)} tokens"
                if caches_at_all(profile, self.model, self.shape)
                else f"**does not cache** — below its {profile.minimum_prefix(self.model)}-token "
                "minimum, so the hit rate above is unreachable here"
            )
            lines.append(
                f"  {profile.vendor}: ${effective:.2f} effective against ${sticker:.2f} at list "
                f"({caching}; TTLs {sorted(profile.cache.write_multipliers)}; "
                f"prices read {profile.recorded_on.isoformat()})"
            )
            run = self.measured.get(profile.vendor)
            lines.append(f"    {_measured_line(run)}")

        if self.cheapest() is None:
            lines.append(
                "  Only one vendor is recorded, so this is a cost model rather than a "
                "comparison. AC 1 needs a second vendor's account and real spend; nothing here "
                "supersedes ADR-002 until it has run."
            )
        return "\n".join(lines)


def _measured_line(run: MeasuredRun | None) -> str:
    if run is None:
        return (
            "not measured: no run against this vendor has happened, so cost per confirmed "
            "finding, experiments to conclusion and the observed hit rate are all unknown"
        )
    parts = []
    parts.append(
        f"${run.usd_per_confirmed_finding:.2f}/finding"
        if run.usd_per_confirmed_finding is not None
        else "cost per confirmed finding not measured (needs E9's finding audit)"
    )
    parts.append(
        f"{run.experiments_to_conclusion} experiments to conclusion"
        if run.experiments_to_conclusion is not None
        else "experiments to conclusion not measurable (S-0.8: the model never concludes)"
    )
    parts.append(
        f"{run.measured_cache_hit_rate:.0%} measured hit rate"
        if run.measured_cache_hit_rate is not None
        else "hit rate not measured"
    )
    return "; ".join(parts) + (f" — {run.note}" if run.note else "")


def cheaper_sticker_can_lose(
    dearer: VendorProfile, cheaper: VendorProfile, model: str, shape: WorkloadShape
) -> bool:
    """Whether the vendor with the lower list price costs more on this shape.

    The Notes' claim, as a function rather than a sentence: *a vendor 30% cheaper
    per token but with a larger minimum cacheable prefix, a shorter TTL, or
    weaker prefix semantics can cost more here.*
    """
    if cheaper.price_for(model).input_usd >= dearer.price_for(model).input_usd:
        message = (
            f"{cheaper.vendor} is not the cheaper sticker for {model}; this asks whether a lower "
            "list price loses, so the argument order matters"
        )
        raise VendorError(message)
    return effective_run_usd(cheaper, model, shape) > effective_run_usd(dearer, model, shape)


def recorded_profile(vendor: str) -> VendorProfile:
    """A recorded vendor, refusing one nobody has measured.

    Raises:
        VendorError: no profile exists. Deliberately unhelpful about inventing
            one — a profile assembled from recollection is the failure this
            story was written to end.
    """
    try:
        return VENDORS[vendor]
    except KeyError:
        known = ", ".join(sorted(VENDORS))
        message = (
            f"no profile is recorded for {vendor!r}. Recorded: {known}. Add one only from "
            "published figures with a date and a source — a profile from memory is what "
            "ADR-002 was already criticised for"
        )
        raise VendorError(message) from None


def price_book_agrees(profile: VendorProfile) -> bool:
    """Whether a profile's prices still match S-5.3's price book.

    A guard against the copy this module deliberately does not make: if a
    profile ever stops being derived and starts being duplicated, this is what
    notices.

    Takes the profile rather than assuming the Anthropic one, so it can be
    exercised against a profile that *disagrees*. A check with one hardcoded
    subject can only ever return `True`, which makes it a guard nobody has
    checked — found by sabotage, and S-3.12's finding for the fourth time.
    """
    return all(
        model in PRICE_BOOK and price == price_of(model) for model, price in profile.prices.items()
    )
