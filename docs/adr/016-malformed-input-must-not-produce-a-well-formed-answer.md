# 016 — Malformed input must not produce a well-formed answer

**Status:** accepted
**Date:** 2026-08-03

## Context

After the five lab-bench instruments were built, they were run against inputs
their tests had not covered: a missing binary, an empty command, a payload that
references itself, a sample containing NaN, a callable faster than the clock, a
consumer holding its own reference to a counted function.

One result was serious enough to justify this record.

```
rank_test([nan] * 8, [1.0] * 8)  ->  p = 0.0004
```

A decisive, well-formed, entirely fictional finding. Every comparison against
NaN is false, so sorting produces an arbitrary order, the tie detector sees no
ties, ranks come out meaningless, and every line of arithmetic downstream runs
to completion. Nothing raises. Nothing is out of range. The output is exactly
the shape of a real result.

That is the failure this project names in its own invariants — *never
manufacture a finding* — arriving not through an agent's reasoning but through
the instrument beneath it, which is the layer specifically built to be trusted.

The others were milder but the same shape. A missing binary raised
`FileNotFoundError`, a bad working directory `NotADirectoryError`, an empty
command a bare `OSError` — three different untyped exceptions for one condition
that a caller driving unfamiliar repositories meets constantly. A
self-referential structure died of a `RecursionError` naming no path.

## Decision

**An instrument that cannot measure says so. It never returns a number that
looks like the others.**

Applied in four places:

- **Non-finite values are refused** by all three entry points of `stats`. A NaN
  or an infinity is a failed measurement, and a failed measurement must not be
  summarized, fitted, or tested. The guard is at the door, because by the time
  it reaches the arithmetic the damage is a plausible number.
- **Failure to start is `ExecutionStartError`**, distinct from a non-zero exit
  and from a timeout. Those describe something that ran; this describes the
  environment being wrong. It carries the original `OSError` as `cause`.
- **`diff` stops at `MAXIMUM_DEPTH` (200)** with a typed error naming the path,
  rather than exhausting the stack around level 490. JSON cannot express a
  cycle; a Python object graph handed to the function can.
- **Caller mistakes raise `ValueError`** — an empty command, a non-positive
  timeout, fewer than one repetition, a negative tolerance. A non-positive
  timeout previously started the process and killed it immediately, reporting a
  timeout for a command never given a chance to run.

**One limit is documented and pinned rather than fixed.** `calls_to` replaces
an attribute, so a consumer that did `from module import work` calls the
original and is never counted. The undercount is silent. It cannot be fixed at
this layer — a name bound at import time cannot be reached afterwards — and
what limits the damage is that nearly every counter worth having wraps a
*method*, where `cursor.execute(...)` looks the attribute up on the class at
every call. Framework hooks (S-14.2) avoid attribute lookup entirely and are
preferred where they exist. There is now a test asserting the undercount, so
the limit is a recorded property rather than a surprise.

## Consequences

**Makes easy.** A caller catches one exception type per failure mode, and the
message says what to do. An agent reading a `StatsError` learns that its
measurement failed; before this it would have read a p-value.

**Makes hard.** `diff` now refuses payloads nesting deeper than 200, which
previously compared successfully to around 400. That is a real capability
removed, and it is the right trade: nothing a service returns nests that deep,
and the alternative is an untyped stack exhaustion.

**Rules out.** Passing NaN through as a sentinel for "did not measure". Any
caller with a missing measurement has to represent it as a missing measurement.

## Provenance

An adversarial pass over Epic 1's five instruments, run before S-1.6 was
started. Two other findings were left as documented limits rather than changed:
`execute` cannot return raw bytes, so a workload emitting binary output cannot
be measured through it; and `diff` holds one `Difference` per difference, so two
wholly different 2,000-element lists produce 2,000 objects. Neither has a caller
yet, and inventing an interface for one would be guessing at its shape.

A third was found by reading rather than probing, and fixed here: the drain
after a timeout kill was unbounded, so a grandchild that inherited the pipe and
survived the kill would hang the function forever — inside the timeout handler
of the one function whose purpose is to bound how long a command may take.
