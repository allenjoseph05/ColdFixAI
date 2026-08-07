# 039 — A sweep is a search, a plan is an opinion, and a revert is checked

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.10 asks that a substitution swap an implementation or configuration value
and re-measure; that configuration substitution support sweeping a range; that
every substitution be reversible and reverted after measurement; and that
query-plan comparison be supported for index hypotheses.

`01-primitives.md` §9 calls configuration the highest-value sub-case — reversible,
no syntax risk, no correctness risk from a malformed edit, bounded search space,
with a widely-cited figure attributing a majority of real performance problems to
it rather than to code. It also names the failure mode: *a substitution faster on
the tested workload may be slower on another.*

## Decision

**A sweep returns a candidate, not a conclusion.** Measuring eight pool sizes
once each is a cheap way to find out which one to look at and nothing more:
eight single samples cannot separate differences smaller than S-0.4's ~20ms noise
floor. `confirm` is what turns the candidate into a claim, by putting it against
the incumbent through S-1.6's interleaved comparison — which takes both variants
as callables precisely so that a stored measurement cannot be passed to it. This
is `01-primitives.md` §12's pattern in a second place: *search first, then
validate the single winner with proper interleaved statistical timing.* The
sweep's own `explanation()` says it is a search result, and repeats §9's warning
that a value tuned on one workload is a claim about that workload only.

**A sweep that recovers the incumbent proposes nothing, and `confirm` refuses
it.** That is a real result worth recording — over the range tried, the
configuration is already at its best — and it is not a comparison, because there
is nothing to compare against.

**Reverting is verified, not performed.** Every substitution restores in a
`finally` and then reads the value back and checks it. A restore that appears to
work and does not is not hypothetical: an object with its own `__setattr__`, a
cached property, or a settings class that validates assignments will accept the
call and keep its own value. Such a substitution raises nothing, stops nothing,
and silently changes every measurement taken afterwards — ADR 008's failure with
the subject's configuration in place of an instrument.

**A value that cannot be read is refused before anything changes**, because
setting first and discovering afterwards that the original is unrecoverable
leaves the subject permanently modified — and for a configuration value,
permanently modified is indistinguishable from always having been configured that
way. Settability is likewise checked by attempting the real assignment, not by
probing with a test write: a probe is a mutation taken before the caller asked
for one, and on a property setter it can have effects of its own.

**Query plans are the planner's opinion and are labelled as such.** `EXPLAIN`
without `ANALYZE` measures nothing, and this project's first non-negotiable is
that there is no finding without a measurement. So a plan comparison reports the
**shape change** — a sequential scan became an index scan — as a fact about the
plan, and its cost numbers as an estimate, with the explanation saying outright
that the workload still has to be timed or counted. `analyze=True` is available
and is not the default, because an `EXPLAIN ANALYZE` of an `INSERT` inserts; the
production guard is what makes that safe rather than harmless.

**An index the planner ignores is a reported outcome.** Adding an index and
seeing the same plan means the planner declined to use it, which is a finding and
not a failed experiment.

**Nested plan nodes are all read.** An index scan under a sort is still an index
scan, and a comparison that looked only at the outermost node would miss every
plan with a wrapper — which is most of them.

## Consequences

**Makes easy.** The configuration half of `01-primitives.md` §9's target list —
pool sizes, batch sizes, prefetch depth, timeouts, compression, debug flags left
enabled — all of which are attributes or mapping entries and all of which sweep
the same way. Index hypotheses, which now have a shape-change fact to rest on.

**Makes hard.** Substituting anything whose original cannot be read back, and
concluding from a plan alone. Both are refused rather than approximated.

**Left open.** Pool sizes and cache TTL are in §9's target list *and* in
`00-BRIEF.md` §4's slack-reducing list, so a sweep can recommend exactly the
change the metastability gate exists to catch. Nothing here classifies that —
S-10.6 owns it — and the sweep result carries the value and the attribute it
changed so that it can.

## Provenance

Five sabotage runs, each asserting the edit was detected: dropping the
restoration check fails 1 test; restoring outside the `finally` fails 1; reading
only the outermost plan node fails 1; letting the sweep leave values in place
fails 2; allowing a no-op confirmation fails 1.

**The first sabotage silently did not apply**, and reported five clean passes
before that was noticed — the edit's target text had been reflowed by the
formatter, so the replacement matched nothing and the suite was run against
unmodified code. This is the failure ADR 024 recorded and the reason this
project's rule is to assert the edit landed rather than to trust that it did. The
runs above each assert it.
