# 036 — Wall minus CPU is exact; attribution by category is not, and says so

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.7 asks for time blocked on disk I/O, network, lock acquisition and scheduler
queueing; for the experiment result to distinguish *computed a lot* from *waited
a lot*; for the measurement to work inside the container sandbox; and for a test
with a deliberate sleep to show blocked time rather than CPU time.

`01-primitives.md` §12 says why the gap matters: without off-CPU instrumentation
**the entire saturation column of the USE Method is unmeasurable**. The story's
note says what it costs in practice — an ablation tells you a component is
expensive and never whether it computed or waited, and those have nothing in
common as fixes. A component burning CPU wants a better algorithm; a component
waiting on a database wants an index, a batch or a different query.

## Decision

**The total is one subtraction and it is exact.** `perf_counter` is elapsed time,
`process_time` is CPU charged to this process, and the difference is time the
process existed and was not running. No sampling, no tracer, no platform-specific
machinery, two clock reads. That is what makes it affordable to record on *every*
measurement rather than only when off-CPU time is already the hypothesis — which
is what AC 2 asks for, and what makes the distinction reach whoever picks the
fix.

**Attribution by category is a different problem, and the honest answer is that
it is partial.** Timing which blocking call waited means instrumenting the call,
and the real ones are not reachable from Python: `io.BufferedReader.read`,
`socket.socket.recv` and `_thread.LockType.acquire` are C types whose attributes
cannot be replaced. So attribution comes from two places and neither is complete:

- **What an adapter declares.** `blocking(owner, attribute, category)` wraps a
  callable the adapter knows is a waiting point and records the seconds it took.
  This reuses S-3.6's magnitude-carrying record exactly — the events are the
  calls, the total is the seconds — so blocked time is read by every primitive
  that reads any other counter, with no second instrument. It records *elapsed*
  time rather than blocked time, which is the right measurement for a point
  chosen because it waits and the wrong one for anything else, so it is for
  points an adapter declares deliberately.
- **What the operating system already counted.** `getrusage` gives voluntary
  context switches (the process gave up the CPU to wait), involuntary ones and
  block I/O counts.

**Scheduler queueing has no hook and never will**, because being preempted is not
a call anything can wrap. It is measured from involuntary context switches, and
`blocking()` *raises* for that category rather than accepting a wrapper — an
adapter that could register one would believe it had instrumented queueing.

**Where a signal is unavailable it is `None`, never `0`.** ADR 013's rule in its
original form. Zero involuntary context switches is a publishable finding —
*nothing was preempted, so the cost is not queueing* — so a platform that cannot
measure must not be able to produce it. `resource` does not exist on Windows;
`scheduler_signal_available` is what a caller reads before the number, and the
explanation says outright that not measuring is not the same as none having
occurred.

**Negative blocked time is reported, not clamped.** CPU exceeding the wall clock
means the work ran on more than one core, so *elapsed minus CPU* stops being time
spent waiting. That is `Boundedness.PARALLEL`, its own answer, because a run that
hits it needs the load primitive (S-3.12) rather than a subtraction. Clamping to
zero would print *never waited*, which is a finding, from a case where the
decomposition does not apply.

**AC 3 was run rather than argued.** The host here is Windows, where `resource`
does not exist at all — so precisely the signals this story leans on are the ones
that cannot be checked locally. The container test writes a probe into the
sandbox and asserts real blocked time, real CPU time and real context-switch
counts from inside it.

## Consequences

**Makes easy.** Reading an ablation delta correctly: the same 200ms disappearing
from a compute-bound component and a blocked one are different findings, and the
result now says which. S-3.12's load work, which needs the queueing signal.

**Makes hard.** Attributing blocked time to a category without an adapter that
names its waiting points. The coarse voluntary-switch count is what exists
otherwise, and it covers every kind of waiting at once.

**Rules out.** Reporting a component as expensive without saying whether it
computed or waited, and reading a `None` signal as a zero.

## Provenance

Four sabotage runs, each asserting the edit was detected: reporting unavailable
signals as zero fails 1 test; clamping negative blocked time fails 1; recording
the blocking time outside a `finally` fails 1 (a call that times out has waited
for exactly as long as the timeout, and that is the finding); dropping the two
metrics from `measure_once` fails 1.

**One test was flaky rather than sabotage-sensitive and was replaced.** Four
Python threads in a busy loop cannot reliably produce CPU time above the wall
clock, because the GIL keeps them on one core — and the test's skip guard did not
match the classification's threshold, so between the two it tested the machine's
scheduler rather than the classification. The parallel case is now constructed
directly, and the threaded test uses `sleep`, which does release the GIL and is
therefore reliable.
