# 115 — The guards are a denylist and the envelope is a longer list

**Status:** accepted
**Story:** S-11.4 — trade attacks
**Date:** 2026-08-18

## Context

`08-audit.md` F10: **guard counters are a denylist.** A guard pair catches the
trade somebody predicted — queries against rows, because someone knew halving one
can explode the other — and catches nothing else.

`CostClaim` requires at least one guard, and that requirement is the strongest
thing a falsification test can do about a trade: it makes the Surgeon name what it
thinks it might break. What it cannot do is make the Surgeon name what it did not
think of, and a patch is written by the party with the least interest in listing
that.

S-3.8 built the answer — a fixed envelope of global resources checked whether or
not a trade was predicted. This story is that check placed where it decides
something, plus the number in between the two mechanisms.

## Decisions

### 1. The one story in Epic 11 that branches

Every other story in the epic depends on S-11.1's isolated context, because every
other story asks a model something. This asks arithmetic of two measurements, so
there is nothing to isolate it from — which is why the backlog's `Depends:` line
reads S-3.8 and not S-11.1.

### 2. The envelope check is S-3.8's, imported

S-3.8 owns the tolerances, the absolute floors, the availability reporting and the
rule that *a rise must clear a ratio and a floor*. A second implementation would be
two answers to a question with one right one. `compare` is imported under the name
`compare_envelope` and nothing here re-derives a threshold.

### 3. The number this module exists to produce is `uncovered`

An envelope resource that rose past tolerance and that **no declared guard was
watching**. That is F10 stated as an observation rather than a warning: the guards
held, and something still got worse.

Usually this is every breach, because guards are declared on domain metrics and
the envelope is global — and *usually all of them* is the finding, not an
artifact of the arithmetic.

### 4. S-3.8 flags only rises; this reports both directions

Its `compare` is a verdict, and a two-sided check would flag every successful patch
for the improvement it was written to make. This is a **report**, and AC 2 asks for
both, because a rise on its own is not a trade.

A resource that rose with nothing falling beside it is a **regression** — a
different sentence to put in front of a human than *it bought its speed with
memory*. An audit that printed only the rises makes the two indistinguishable, so
`Trade` carries `is_a_trade` and `is_a_regression` separately.

### 5. An unevaluated guard is not a guard that held

`GuardOutcome.held` is `None` where the metric was not measured on the patched run,
never `True`. That is the denylist failing in its quietest form: the metric
somebody *did* think of, and still no answer about it.

`TradeAudit` also refuses to be built if a declared guard is missing from its
report, for the same reason.

### 6. Unmeasured is not within tolerance

Three envelope metrics need `/proc` or `getrusage`. `clean` requires `complete`, so
a run that checked five of eight does not report a clean envelope. In the Linux
sandbox everything reads, so this only bites on the development host — which is
exactly the run whose result should not be trusted. S-11.2's `survived` and
S-11.3's `complete`, for the third time in the epic.

### 7. The two metric sources merge in one place and never shadow

Envelope samples and domain counters stay separate arguments and are merged only
inside `_movements`. A single mapping would let a domain counter named
`wall_seconds` overwrite the envelope's — silently, and in the direction of the
patch looking better, since a domain timer measures the window and the envelope
measures the whole process. A name in both is reported twice, the domain one
prefixed `workload.`, rather than one winning.

## Consequences

**A test helper that silently did nothing, in seven tests at once.** `sample()`
took keyword arguments — `sample(THREAD_COUNT=64.0)` — but the metric names are
module constants whose *values* are the keys. The call bound a key spelled
`THREAD_COUNT`, left `thread_count` at its quiet value, and produced a sample that
looked overridden and was not. Every test that thought it was raising a resource
was passing an unchanged envelope to the code under test. The helper now takes a
mapping, where the constants are used as what they are.

**Both sabotage survivors were fixtures that could not discriminate**, which is the
same finding S-11.1 and S-11.2 each ended on:

- every claim declared its guards on **domain** metrics, where *breaches not
  covered by a guard* and *all breaches* are the same list — so skipping the
  coverage check entirely changed no assertion. A guard aimed at an envelope
  resource is what separates them;
- every `Guard` was built with `at_most` equal to `baseline`, so comparing a
  measurement against either gives the same answer, and a sabotage swapping one for
  the other survived. A **tolerated regression** — `baseline` 1000, `at_most` 1200,
  measured 1100 — is the only shape that tells them apart.

**The substring-over-source check was not written this time.** The reuse test
asserts on the module's bindings instead; `"def compare" not in source` would have
matched `compare_envelope` on its own name, which is the S-11.3 trap arriving one
story early enough to avoid.

**Sabotage: 34 properties, all caught, zero skipped, after two survived.**
