# 060 — The cheap tier is the hardest to cache

**Status:** accepted
**Story:** S-5.7 — cache-friendly context assembly
**Date:** 2026-08-09

## Context

`04-cost.md` §12.2 makes prompt caching the single largest cost variable in the
system: the investigate loop at 120 calls costs ~$39 uncached at 60k context and
~$1.68 pruned and cached — **23× from one variable**. §4 records that as an
architectural rule rather than an optimisation, and `CLAUDE.md` carries it as a
non-negotiable.

Every way of losing it is **silent**. The request succeeds,
`cache_read_input_tokens` comes back zero, and the bill goes up. There is no
error to catch and nothing in the response that says caching stopped.

## Decision — the structure, and why it fits exactly

Caching is a prefix match: any byte change anywhere in the prefix invalidates
everything at or after it. Render order is `tools` → `system` → `messages`, so
AC 1's five segments are in the only order that works.

A request allows **four `cache_control` breakpoints**, and AC 1 has exactly four
cacheable boundaries — system, playbook, source, log — with the varying question
after the last of them. That is not a coincidence to be relied on quietly: the
question is never given a breakpoint, because caching it would write an entry no
later call can read and spend one of only four doing it.

## Decision — AC 2 is an absence, not a discipline

There is no method that reorders the log, none that re-summarizes it, and none
that changes a stable segment. `Investigation` captures its system prompt,
playbook and source at construction, and `append` is the only way anything grows.

That is what makes the prefix byte-identical rather than merely intended to be:
the classic silent invalidator is a `datetime.now()` in the system prompt, and
captured once at construction it is evaluated once. Re-rendered per call it would
move the prefix every time and nothing would ever cache.

## Decision — the minimum cacheable prefix is not monotonic, and the cheap tier is worst

| Model | Minimum cacheable prefix |
|---|---:|
| `claude-opus-5` | 512 |
| `claude-sonnet-5` | 1024 |
| `claude-haiku-4-5` | **4096** |

A prefix below the model's minimum does not cache **at all**, with no error.

S-5.5 routes grounding's mechanical work to `claude-haiku-4-5` precisely because
it is cheap, and §12.3's engineered grounding is *ten calls with a mature
playbook* — a short prompt. At, say, 1,000 tokens that prompt caches on the
frontier model and not on the cheap one. **Routing a step down a tier can
therefore raise its effective cost**, which is the exact opposite of what the
routing exists to do, and nothing anywhere reports it.

So `minimum_prefix` refuses a model it has no figure for rather than defaulting —
the failure it guards is silent, and a default would hide it — and the
below-minimum message names the routing interaction explicitly, because a reader
who has just been told to use a cheaper model will not otherwise connect the two.

## Decision — the log is one block, because of the 20-block lookback

A breakpoint walks back **at most 20 content blocks** looking for a prior cache
entry. Past that the next request finds nothing and silently pays full price.

An experiment log rendered one block per experiment crosses that at experiment 21
— and S-5.4 caps investigation at **40 experiments**, so the obvious
implementation stops caching *exactly halfway to its own budget*. The log is
therefore rendered as one growing block, which is also what makes append-only
checkable: the log at call N is a byte prefix of the log at call N+1.

## Decision — token counts are measured, never estimated

`viability()` takes a count from `messages.count_tokens` and returns `UNKNOWN`
without one. `tiktoken` is OpenAI's tokenizer and undercounts Claude by 15–20% on
prose and considerably more on code; `CLAUDE.md` forbids reporting a measurement
nobody took, and here the measurement is *whether a cost control is working*.
S-4.5's rule again: *could not tell* stays distinct from *nothing wrong*.

## Consequences

**The first call of an investigation can never hit**, and it pays the 1.25× write
premium on top. Reporting only a blended hit rate would make a perfectly working
cache look worse the shorter the investigation, which is backwards — so
`warm_hit_rate()` excludes it and the report says which is which. The report also
names the tokens *written*, because a write bills above the uncached rate
(ADR 056) and a figure showing only reads makes caching look free.

**Two guards were unreachable and are now reachable.** The breakpoint and
lookback limits sat inside `render`, which builds a fixed five-block tuple — so
neither could ever fire and a sabotage removing either passed. They are now a
`check_blocks` function that `render` calls and a test exercises directly. S-3.12
recorded the same finding once already: a guard no test reaches is a guard nobody
has checked.

**Sabotage found three real gaps out of twenty-three**, the highest proportion in
Epic 5. Besides the two unreachable guards, `stable_prefix()` had only been
asserted against itself, so dropping the source segment from it changed no test —
a prefix missing a segment would report *byte-identical* while the omitted
segment moved freely. A fourth survivor turned out to be redundant code (`not
warm` where an empty slice already sums to zero) and was removed rather than
tested. All twenty-three are caught now.
