# 142 — A quiet machine is a dependency, like a database

**Status:** accepted
**Date:** 2026-08-25

## Context

`CLAUDE.md` requires the fast subset green before a story is called done. It was
not reliably green. Five sightings across three months, always attributed the
same way: re-run the failing test standalone, check that the change in hand could
not have reached the failing module, re-run the whole subset. Three seven-minute
runs, paid on every story, and the only thing standing between a flake and a
laundered regression.

S-0.9's note called the fix *a decision, not an obvious fix* and listed three
candidates. The first question turned out not to be which fix but **which
tests** — and that is measurable.

## Decisions

### 1. Build the load generator first, which is AC 3 and made the rest evidence

**Processes, not threads, and the first attempt got it wrong.** Spinning Python
threads all contend for one GIL, so thirty-two of them occupy about one core:
against the four known-fragile tests that load produced **one failure in sixty
runs**. Separate processes occupy cores, and at twice the core count the same
four failed 0/5, 2/5, 4/5 and 5/5.

**A full heap was the wrong hypothesis, and worth ruling out.** The flakes appear
in the long single-process subset run and not standalone, so accumulated objects
and a generational collection landing inside a timed measurement was the obvious
suspect — `bench.timing` deliberately does no garbage-collection control.
Carrying six thousand live blocks through a session produced one failure in
twenty; oversubscribed cores produced eleven in twenty on the same tests.

**One mechanism explains all of it: wall time inflates under contention and a
count does not.** Every failure examined reduces to that. `off_cpu` classifies a
half-compute block as blocked because elapsed grew and CPU did not.
`instructions.hidden_work` decides a pure-Python workload ran in C for the same
reason. `test_epic_composed` asserts a wall-clock gap stays under S-0.4's 20ms
floor and measures 21.4ms. That is one finding, not eight.

### 2. Quarantine is the last resort — construct where the subject is logic

`test_off_cpu.py` already contained the answer, two tests below the flakiest one:

> Constructed rather than provoked. Four Python threads in a busy loop cannot
> reliably produce this condition … and a test that sometimes lands between
> "parallel enough to classify" and "parallel enough to skip" **tests the
> machine's scheduler rather than the classification**.

`test_a_run_that_does_both_is_reported_as_mixed` provoked its condition with a
real half-busy, half-sleeping block and failed **5/5** under load. Its subject is
the boundary arithmetic, so it now constructs an `OffCpuProfile` directly and
stays in the gate; the empirical claim is kept beside it as a `timing` test rather
than deleted.

The rest are genuinely claims that a primitive *measures* what it says. A
constructed version of *a leak shows as growth against elapsed time* would assert
that arithmetic works on numbers the test wrote itself, which is not the claim.
Those are quarantined, not rewritten.

### 3. A fourth marker, because the reason is a fourth reason

`pyproject.toml` already keeps `slow`, `docker`, `postgres` and `index` apart,
each with a comment saying why conflating it with the previous would hide
something. Same argument again: these are **not slow**, a daemon would not help,
and they pass alone. What they need is a **quiet machine**, which is as much an
environmental dependency as a Postgres server.

`timing` joins the family and the gate becomes `-m "not slow and not timing"`.
Marking them `slow` would have been convenient and false, and the next person
reading `slow` would have waited for tests that take fifteen seconds.
They still run: `-m "timing"` is the deliberate pass on a quiet machine, exactly
as the Postgres tests are run deliberately with a database up.

### 4. The marked set is a function of the load, and the load is a knob

This is the part that changed during the story and matters most.

| round | what was swept | found |
|---|---|---|
| 1 | `tests/primitives`, one run at 2× | 13 |
| — | plus two known from sightings that this run did not fail | 15 |
| 2 | `tests/primitives`, five runs | 1 (`test_envelope`) |
| 3 | `tests/primitives`, five runs | 2 (`test_instructions`, `test_epic_composed`) |
| 4 | `tests/primitives`, five runs | **0** |
| 5 | `tests/bench`, `tests/replay`, three runs | 3 |
| 6 | the same, three runs | 1 |
| 7 | the same, three runs | 1, in `tests/explorer` |

Every sweep at 2× surfaced roughly one more, somewhere, at a rate of one failure
in three to five runs. **At that load the tail does not terminate**, because
twice the core count is harsh enough that any test with a subprocess timeout can
eventually fail — the last one found is a `docker ps` call, which is not a timing
assertion at all.

So the acceptance bar is **one spinner per core**: a fully busy machine, which is
what a parallel build or a second test session produces and what the five real
sightings actually happened under. 2× remains available and is the right setting
for *hunting*, which is what it was built for.

**The durable deliverable is the harness, not the list.** The list is a reading of
the knob.

### 5. AC 2 is satisfied by the partition rather than by a noise floor

The criterion is that no timing test asserts the **absence** of an effect against
a fixed threshold without a measured noise floor. Every instance found — the two
`assert not result.detectable` in `test_isolation.py`, the envelope control, the
20ms-floor assertion — is now outside the gate and runs on a quiet machine, where
the threshold is what it was certified against.

`bench.certification.certify` exists and would give each a measured floor, at 25
baseline samples plus 200 bootstrap trials per test. That is the right instrument
for an *investigation*, which certifies once and runs many experiments; spending
it per assertion in a unit suite buys a much slower suite and the same answer.
Recorded as the road not taken, because it is the fix the story's note proposed
first.

### 6. A sweep that reports clean is worth distrusting once

One sweep of `tests/bench` came back 3/3 clean and was wrong: it ran from stdin,
and Windows spawns child processes by re-importing the main module, so the
spinners died with `OSError: Errno 22` and the runs measured a quiet machine.
Re-run from a file with the same targets, it found three failures immediately.

The harness now prints how many spinners are actually alive, because a load
generator that silently generates no load turns every sweep into a false negative
— which is the same failure mode as an agent reporting a measurement it did not
take.

## Consequences

- **The gate is honest at the load it claims.** Green means green on a busy
  machine; a failure in it is evidence about the change rather than about the
  afternoon.
- **The three-run attribution ritual is gone.** A load-sensitive test is now
  demonstrated on demand in seconds rather than discovered over months.
- Twenty-two tests moved out of the fast subset into `-m "timing"`, across six
  modules; one moved back in by being constructed rather than provoked.
- `tests/fixtures/contention.py` is test infrastructure and deliberately not a
  primitive. `primitives/load.py` drives a *subject* at concurrency levels to fit
  a scalability curve; this makes the *machine* noisy to find tests that depend on
  it. Same word, two jobs.
- **`tests/explorer/test_standup.py::test_ps_lists_what_is_running` is recorded
  and not marked.** It failed once at 2× and the mechanism was never
  characterised; it is a `docker ps` subprocess and the failure is more likely a
  timeout than a timing assertion. Marking it would have been marking on a guess,
  which is what this story spent its first hour avoiding.
