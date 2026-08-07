# 017 — An instrument must survive the input it cannot summarize

**Status:** accepted
**Date:** 2026-08-03

## Context

ADR 016 hardened the five lab-bench instruments against *malformed* input — a
NaN, a cycle, a missing binary. A second pass asked a different question: what
happens on input that is perfectly well-formed and merely large, or merely
awkward, of the kind an unfamiliar repository supplies without meaning anything
by it.

Three answers were wrong.

**`execute` captured output without bound.** A script writing in a loop handed
back 80,400,000 characters and would have kept going. `subprocess.communicate`
accumulates the whole stream in memory, so a repository with a debug log left
on, or a test suite run verbose over a large fixture, grows the harness until it
dies — and it dies *before* the timeout can fire, so the one mechanism that
exists to bound a runaway command cannot help. A timeout bounds how long a
command may run. Nothing bounded how much it may say.

**`fit_growth` refused a metric that was zero at any scale.**

```
fit_growth([10, 20, 40], [0.0, 2.0, 6.0])  ->  StatsError: metric 0.0 ... is not positive
```

Zero at the smallest scale and growth after is an ordinary shape for a count — a
cache that covers the small case, a queryset that never fires. The power fit
genuinely cannot be taken through a zero, because it runs through logarithms.
The *linear* fit can, and it is perfectly good: slope 0.2, r² of 1.0. The call
refused anyway and returned nothing.

**`execute` inherited stdin.** A command that reads it blocks on whatever the
harness happens to be attached to, spending its entire timeout on a prompt.

## Decision

**An instrument bounds what it accepts, and degrades to the part it can still
compute rather than refusing the whole call.**

- **`execute` bounds each stream at `max_output_chars`** (8,388,608 by default),
  keeping the head and the tail and dropping the middle, with the elided count
  on the result as `stdout_dropped_chars` / `stderr_dropped_chars`. Which end
  matters depends on the failure — a compilation error is at the top, a
  traceback and a test summary are at the bottom — and a stream long enough to
  be elided is one nobody reads in full. **There is no unlimited setting.**
- **Reading is bounded by the same deadline as running.** The pipes are drained
  on threads; if the process exits but a grandchild still holds a pipe, EOF
  never comes, and the call now expires on the caller's timeout instead of
  waiting forever. `timeout` means a bound on the call, not merely on the child.
- **`fit_growth` returns the linear fit and leaves `exponent`,
  `power_r_squared` and `growth` as `None`** when a value is not positive.
  Growth is *not* inferred from the line in that case: the thresholds are
  defined on the exponent, and classifying by a second rule under the same name
  would make two findings that read `LINEAR` mean different things.
- **`stdin` is `DEVNULL`**, so a command that reads it gets EOF.

Decoding moved out of `subprocess` and into an incremental decoder here, because
a 64 KiB read boundary can fall inside a multi-byte character; decoding each
chunk independently corrupts one character per chunk, which arrives downstream
as a `diff()` difference that is not real.

## Consequences

**Makes easy.** A workload from an unknown repository cannot exhaust the harness
through either channel it controls — how long it runs, or how much it prints.
An agent screening a repository whose counts start at zero gets a slope instead
of an exception.

**Makes hard.** `Fit.growth` is now `Optional`, so every consumer has to handle
the unclassified case. That is the intended cost — the alternative is a
classification that was guessed. `execute` no longer returns the complete output
of a command that exceeds the bound, and a caller diffing full output has to
check `truncated` first.

**Rules out.** Capturing a stream in full as a way of proving two runs produced
identical output, past 8 MB. Output equivalence at that size needs a digest
taken as the stream is read, not a comparison of two retained copies — and that
is a real gap, recorded here rather than solved, because no caller needs it yet.

**Accepts.** A grandchild that survives the kill and holds a pipe open leaks a
daemon reader thread for the life of the process. Strictly better than the
previous behaviour, which was to block forever in the timeout handler.

## Provenance

A second adversarial pass over Epic 1, run before S-1.6 and prompted by asking
what an unseen repository supplies that the fixtures do not. The memory bound is
verified by a test that asserts peak allocation, and that test was checked to
discriminate: removing the drop makes it fail at 16 MB against a 100,000
character budget.
