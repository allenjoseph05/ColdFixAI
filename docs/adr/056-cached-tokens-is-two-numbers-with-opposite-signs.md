# 056 — "Cached tokens" is two numbers with opposite signs

**Status:** accepted
**Story:** S-5.3 — token accounting
**Date:** 2026-08-09

## Context

S-5.3's first acceptance criterion lists the fields every model call records:

> phase, agent, step type, model, input tokens, output tokens, **cached tokens**, cost

Read literally, that field list cannot produce a cost.

The API reports two cache figures, not one — `cache_creation_input_tokens` and
`cache_read_input_tokens` — and they bill in **opposite directions**:

| Token kind | Multiplier on the input rate |
|---|---|
| Uncached input | 1× |
| **Cache write, 5-minute TTL** | **1.25×** |
| **Cache write, 1-hour TTL** | **2×** |
| Cache read | 0.1× |

A cache write costs *more* than not caching at all. A million write tokens and a
million read tokens are both "a million cached tokens" and differ in price by
12.5×. No arithmetic recovers a bill from the collapsed figure.

The error is also **signed the flattering way**: caching is understood as a
discount, so a single `cached_tokens` field priced at the read rate under-bills
every cold prefix — which is the first call of every investigation.

The same trap sits one field earlier. **`input_tokens` is the uncached remainder,
not the prompt.** The whole prompt is
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. A field
called `input_tokens` beside a field called `cached_tokens` reads as *the prompt,
of which some were cached* — so a reader who adds them double-counts and a reader
who does not under-reports.

## Decision

**There is no `cached_tokens` field.** `TokenUsage` records the API's four
figures under the API's own names, plus the cache TTL that selects the write
multiplier. `prompt_tokens` is a property that spells out the sum, so anything
reporting prompt volume has a correct number to reach for.

**The TTL is recorded per call, not assumed.** 1.25× against 2× is a 60%
difference on every cached prefix, and a run that used the hour TTL priced at the
five-minute rate is under-billed by that margin with nothing in the totals
looking wrong.

**Cost is computed, and `ModelCall` has no cost field to set.** `CLAUDE.md`
forbids an agent reporting a measurement, and a cost is a measurement — of tokens
against a price. This is S-4.1's construction for `work_verified`, applied to the
number with the strongest incentive to be wrong: a cheaper run looks like a
better one. Cost is a property over usage and the price book.

**An unknown model is refused, not defaulted.** Fast mode on `claude-opus-5`
bills at $10/$50 rather than $5/$25 — the same model id at twice the rate — so
the book is keyed on the *billing* identity and `claude-opus-5/fast` is its own
entry. A default rate would produce a bill indistinguishable from a real one, and
the first unlisted model this system meets will be a newer, dearer one, so the
default under-bills.

**The price book carries the date it was read.** ADR-002 says to treat its rates
as a planning input and re-check before publishing any cost figure;
`PRICE_BOOK_AS_OF` is what makes a stale table visible rather than merely wrong.
ADR-002's indicative table was checked against the current published rates and is
**correct as written** for the three models it lists — no correction needed.

**Money is `Decimal`.** A run is ~250 calls, a project ~1,000 runs, and §12's
figures are quoted to the cent — which binary floating point does not represent.

## Decision — euros need a rate and a date

AC 3 asks for euros; the vendor bills in dollars. `ExchangeRate` carries the rate
**and the day it was true**, and it is a required input to `RunReport` rather than
a constant in the module. A hardcoded rate is correct on the day it is written
and silently wrong every day after, and a cost figure is exactly the kind of
number quoted a year later. The report renders both the rate and the price book's
date, for the reason every other precondition in this project travels with its
result.

## Decision — the denominator can legitimately be zero

`eur_per_confirmed_finding` returns `None` when the run confirmed nothing.

Not zero, not the run total, not infinity. **A run that confirms nothing is a
successful run** — S-4.5 ships *screened nine workloads, nothing found* as an
answer — so the cost is real and the ratio is undefined. That is a different
statement from *it cost nothing* and from *it cost everything*, and it is the same
rule S-4.2 applies to a metric that starts at zero: `None`, because dividing by it
would put a made-up number where a fact should be. The rendered report says so in
words, because a reader who sees no per-finding figure must not read it as a free
run.

## Consequences

**Not every call belongs to a finding, and the unattributed remainder is
reported.** `04-cost.md` §11: grounding happens once per repository, not once per
finding. Splitting it across findings makes each finding look dearer than it was;
demanding a finding id would force an invented one that collects the whole
grounding bill. So `finding_id` is optional, `unattributed_usd` holds the rest,
and `Ledger.reconciles` asserts that the per-finding table plus the remainder
equals the run total — trivially true today, and the only thing that will notice
the first time a call is attributed two ways or a phase is dropped from a cut.

**`Phase` has no screening member.** Epic 4 is *zero model calls*, asserted
structurally, so a phase for it would be a slot that can only ever be filled by a
bug.

**`StepClass` is recorded here and routed on by S-5.5.** §12.3 splits the
investigate loop 15 creative to 105 mechanical; that ratio is a claim about a real
run which nothing could check until calls were counted by class.

**This module must never make a model call**, and a test walks its import graph to
say so. A cost ledger that could call a model could bill itself, and the run
report is the one artifact whose numbers nobody would think to question.

**Sabotage-verified on twenty-three properties, all caught** — including the one
that matters most: pricing a cache write as a discount, which is the exact shape
of the defect a single `cached_tokens` field would have shipped.
