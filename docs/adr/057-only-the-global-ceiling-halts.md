# 057 — Only the global ceiling halts

**Status:** accepted
**Story:** S-5.4 — budget enforcement
**Date:** 2026-08-09

## Context

S-5.4's third acceptance criterion reads:

> Exhaustion halts, checkpoints, and reports — it does not warn and continue

Applied to every cap, that sentence makes three of the four wrong.
`02-architecture.md` §7.2 gives each phase its own disposition, and only one of
them is a halt:

| Phase | Cap | On exhaustion |
|---|---|---|
| Ground | 60 steps | abort with a diagnostic |
| Investigate | 40 experiments | **emit the partial chain with its exclusions** |
| Repair | 3 attempts | escalate with the history |
| Audit | 2 rounds | escalate |
| Global | euro ceiling | halt, checkpoint, report |

The investigate row is the one that matters. Forty experiments that established
something and ran out is **an answer** — *here is what we measured, here is what
this run therefore does not cover* — and it is the same answer S-4.5 ships when a
screen finds nothing. Halting there discards everything the forty experiments
proved, which is the most expensive thing in the run to throw away.

## Decision

**Exhaustion carries a `Disposition`, and the halt belongs to the global ceiling
alone.** `dispositions()` makes the four enumerable rather than something a
reader has to notice, because a handler written against a single outcome is
silently wrong for three of the six phases — and silently in the direction of
discarding a real result.

AC 3's other two clauses hold everywhere: exhaustion raises rather than returning
a flag, and the exception carries an `Exhaustion` with the counters, the
disposition and the spend at the moment it stopped. That follows S-1.7's recorded
argument for `NoiseFloorTooHighError` — refusing by return value lets a caller
ignore the refusal, and refusing without the evidence makes it unloggable. That
record is also AC 3's checkpoint; it is deliberately **not** a checkpoint schema,
which is S-6.1's artifact and is not guessed at here (S-1.7's precedent again).

## Decision — four caps, four units, two scopes

**The units differ and conflating them is a 3× error.** Ground counts steps,
investigate counts *experiments*, repair counts attempts, audit counts rounds.
`04-cost.md` §12.1 budgets 120 model calls per finding in investigate against a
cap of 40 experiments — so an experiment is roughly three calls, and a cap
counted in calls would stop investigation at a third of its intended budget. This
is S-4.4's finding recurring (*the unit is a workload, not a flag*), which is why
`Cap` carries its unit rather than assuming one.

**The scopes differ too.** Grounding happens once per repository (§11), so its 60
steps are counted per run. Investigate, repair and the audits are per finding,
because §12.1's table is written per finding. Both directions are wrong in a way
that looks fine: a run-wide investigate counter gives five findings eight
experiments each, and a per-finding ground counter re-grants 60 steps for every
finding the run opens — a cap that *rises* with the number of findings.

**Each audit phase gets its own two rounds** rather than sharing a pool. The
three audits ask different questions of different artifacts — E9 audits a
finding, the test audit audits a falsification test, E11 audits a patch — and a
shared pool would let a patch audit spend rounds a finding audit had not reached.

## Decision — caps are in code, and may only be lowered

The backlog note is explicit: *caps must be in code, not configuration; the worst
case without them is unbounded*. `PHASE_CAPS` is a module constant read from no
file, environment variable or constructor argument, and both the constructor and
`tighten()` refuse a limit above the compiled figure — so the rule holds for a
running process, not just for the source file.

Lowering is permitted. The asymmetry is deliberate: a run that wants to spend
less than the compiled cap is not the failure mode this exists for, and
forbidding it would make the caps unusable on a cheap smoke test.

## Decision — a ceiling is checked before the spend, on a pessimistic estimate

Cost is known only once a call returns, so a check afterwards reports a breach
rather than preventing one. `authorize()` therefore takes the worst case the step
could cost and refuses on the projection. `worst_case_usd()` prices every prompt
token as a **one-hour cache write — 2× the input rate, the dearest an input token
can be (ADR 056)** — and assumes the whole of `max_tokens` comes back. Both are
pessimistic on purpose: a ceiling enforced against an optimistic estimate holds
only when the caching went well, which is the run where a ceiling matters least.

## Consequences

**Progress is measured from the conclusion, not from the agent's opinion.**
§7.2's progress check asks whether the last N steps produced new information, and
*did that step teach me something* is exactly the self-judged criterion
`08-audit.md` F6 removed once already. So a step is recorded with a digest of its
**conclusion** — a growth class, a flag set, a verdict — computed by the harness
from the artifact. The digest is over the conclusion rather than the measurement
for S-5.2's reason: every measurement carries durations, no duration repeats, and
a digest over raw numbers would never detect a stall at all.

**A stall and an exhaustion are different exception types.** They call for
opposite actions — exhausted means stop, stalled means change approach while
budget remains — so a caller catching one would handle the other wrongly.

**The default stall run is three, not two.** Confirming a result twice is
something an investigation legitimately does; a run of two would escalate on the
confirmation.

**A step with no conclusion resets the run rather than extending it.** A failed
experiment is not a repeated one, and extending would let a phase escalate on
steps that never claimed to establish anything.

**Sabotage-verified on twenty-four properties, all caught — but only after a test
was strengthened.** Re-scoping the patch audit from per-finding to per-run
survived the first pass, because the test compared two *phases* on one finding
and nothing had been recorded against the phase under test. The property is only
visible once two findings compete for the same counter. Fourth time in this
project that a passing sabotage meant the test was weak rather than the code.

**The test-file name collision was hit for the third time.** `tests/cost/`
originally held a `test_budget.py`, which collides with
`tests/screening/test_budget.py` under mypy — the same trap recorded in Epic 3's
and Epic 4's composition checks. Renamed to `test_enforcement.py`.
