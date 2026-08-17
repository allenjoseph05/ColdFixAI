# 052 — The N+1 is linear, so "superlinear" is the wrong test

**Status:** accepted
**Story:** S-4.3 — flagging and ranking
**Date:** 2026-08-08

## Context

S-4.3's first acceptance criterion: *flags superlinear growth and unexplained
high flat cost.*

A textbook N+1 grows **linearly** in query count. `queries == 1 + A` is linear in
this project's planted fixture, and ADR 011 records the unplanted defect in the
development target as *query count scaling with rows returned* — also linear. A
screen that flagged only a fitted exponent above one would clear the single
defect this system was built around, on the repository it was pinned against.

The trouble is that *superlinear* compares a metric against the wrong thing.

## Decision

**Flag growth above what a metric can be expected to do**, which differs by
metric:

- **A round-trip count** — queries, requests, file opens — is expected to stay
  **constant**. One batched round trip serves a hundred rows as easily as ten,
  and producing exactly that is what a fix for an N+1 *is*. A count that climbs
  with the data is the shape being removed.
- **An amount** — rows, bytes, allocations — and **a duration** are expected to
  grow **linearly**. More data is more data.
- **Anything unrecognised** gets the linear expectation, so it is flagged only
  when superlinear. That is AC 1 read literally, kept as the safe default for a
  metric nothing is known about.

The expectation is read off `counters.py`'s existing vocabulary — `Reading.EVENTS`
is a round trip, `Reading.TOTAL` is an amount — so nothing new has to be
maintained alongside the catalogue. Superlinear growth in an amount is still
flagged, so nothing AC 1 asked for is lost.

**A duration also has to clear S-0.4's noise floor**, and this was found by the
screen flagging its own control. The batched workload — the fixture's clean
counterpart, the shape a fix produces — came back `SUPERLINEAR` in `seconds` at
8.7× across a sixteenfold sweep, on a workload that runs in under a millisecond.
Screening takes one sample per scale point, and S-0.4 measured timings drifting
12% between runs minutes apart, so a fitted exponent over four single samples of
a sub-millisecond workload is a fit to noise.

So a duration flag needs the two tests S-3.8's envelope already applies to a
candidate: the shape *and* an absolute rise above the ~20ms floor. Counts are
exempt, because they reproduce to the integer. Durations are still measured,
fitted and reported; they cannot raise a flag alone.

**High flat cost is a separate, weaker kind.** The word doing the work in AC 1 is
*unexplained*, and screening has no way to establish whether an explanation
exists. S-0.3 measured a ~35-query floor on a real mature system's endpoint, and
this project's fixture ships a 37-query decoy that must never be called a defect
— a fix there is the metastability trap `00-BRIEF.md` §4 warns about. So:

- `FLAT_COST` is its own flag kind, ranked below every growth flag;
- the threshold is 120 queries, more than three times the measured floor, so the
  decoy sits well clear of it;
- the explanation says the workload did not grow, that it may be correct, and
  that removing slack rather than waste is the trap;
- only counts qualify. A flat 200ms is a fact about one machine on one afternoon.

**The ranking states what it cannot express.** `08-audit.md` §6 gives the case: a
tenfold win on a monthly batch job sorts above a twofold win on the busiest
endpoint, and there is no call-frequency information anywhere in this system.
Every report carries that sentence, because an ordering that looks like a
priority *is* a priority to whoever reads it.

Growth flags outrank flat-cost flags as a class, then magnitude within each. The
two magnitudes are a ratio and a count — different units with no honest exchange
rate — and ordering by kind says the true thing: a metric watched across a
sixteenfold increase and found to grow is stronger evidence than a number that
crossed a threshold somebody chose.

## Consequences

**Three answers, not two.** A metric whose growth could not be fitted at all —
zero at some scale point, so no exponent, because the power fit runs through
logarithms — is neither flagged nor cleared. S-4.5 needs *could not tell* kept
apart from *nothing there*, and a screen that folded them together would publish
an exclusion nobody measured.

**A CPU-bound defect with no counters is harder to catch here.** Its evidence is
a duration, and a duration under the noise floor cannot flag. That is correct
rather than unfortunate — S-3.19 exists because timing cannot resolve below
~20ms, and the right instrument for that case is an instruction count in
diagnosis rather than a louder screen.

**Two tests in this story were noise-dependent and are now stated.** The batched
control's `seconds` fit landed superlinear on one run and linear on the next, so
a sabotage removing the duration guard passed — the test would have caught it
only on an unlucky afternoon. Both duration rules are now exercised against a
stated growth class with the absolute rise set on either side of the floor.
