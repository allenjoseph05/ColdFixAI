# 049 — The deterministic unit is a bytecode instruction, and two corrections make it one

**Status:** accepted
**Story:** S-3.19 — deterministic instruction counting
**Date:** 2026-08-08

## Context

S-0.4 measured the timing noise floor at roughly 20ms, about 6% of a 350ms
endpoint, at 20 repetitions. A real 2% improvement is therefore invisible to
timing however many samples are taken, and `01-primitives.md` §12 names the way
out: *search against instruction count, then validate the single winner with
proper interleaved statistical timing.* That makes this the enabling primitive
for any optimization search rather than another instrument.

§12 has callgrind in mind, which counts retired **machine** instructions.
Callgrind requires valgrind and does not exist on this platform, and the subject
is Python: a machine-instruction count of a CPython process is dominated by the
interpreter loop rather than by the program under test.

## Decision

**The unit is a CPython bytecode instruction**, counted through PEP 669's
`INSTRUCTION` event. What §12 actually asks for is a metric that is independent
of machine and load and reproduces run to run, and this one is. Measured here,
`for i in range(n): total += i` costs exactly `24 + 7n` instructions — the same
number every run, in a fresh process, and under three different values of
`PYTHONHASHSEED`.

**The stated reproducibility tolerance is zero.** Counts are equal to the
integer or the instrument is broken. A tolerance would give away the only reason
to have it.

**An instrumented run reports no duration at all.** Counting costs about 33× the
run, so a `seconds` measured under it is a fact about the instrument.
`InstructionCount.metrics` omits every duration key rather than documenting a
caveat, so no caller can compare an instrumented time against a clean one.

Two corrections were needed, and both were found by measurement rather than by
reasoning.

**The harness's own drain is subtracted, and recorded.** Forcing a lazy result
means iterating it, and `drain`'s loop costs bytecode per item. Counted naively,
`sorted(range(50_000, 0, -1))` retires 300,096 instructions — for a sort that
happens entirely in C. Draining a filler list of the same length costs 300,087,
so the subject's share is **9**. Without this, result size masquerades as work,
and the visibility guard below is defeated by any workload that returns a large
list. `drain_instructions` is on the result rather than folded away: a
subtraction nobody can see is a number nobody can check.

**The interpreter is warmed before counting.** The same workload counted 1311,
then 787, 787, 787, 787. `isinstance(result, Iterable)` runs `__subclasshook__` —
Python code — once per result type and caches the answer forever after. That
one-time work belongs to the process, and left in it lands on whichever variant
of a comparison ran first, which is a systematic bias in favour of the second.
So the workload runs once untouched first; that run also supplies the reference
timing.

**`hidden_work` at one million instructions per second.** An interpreter running
flat out manages on the order of 10⁸ — measured here at 96 million. A workload
retiring less than a hundredth of a percent of that spent essentially all its
time somewhere this instrument cannot see: inside a C function, blocked on I/O,
or waiting on a lock. Two orders of magnitude of slack, because the threshold
only has to separate *the interpreter ran this* from *the interpreter watched
this*.

**Only monitoring tool ids 3 and 4.** PEP 669 assigns 0, 1, 2 and 5 to debuggers,
coverage, profilers and optimizers. Taking one would work and would break
whatever it belongs to.

## Consequences

**The count is of a warm subject.** A subject that caches its own work has that
cache warm when it is counted, and keeping it out is the caller's business
exactly as it is in S-3.2. This is the one place where the reproducibility
requirement and the cold-cache requirement point in opposite directions, and
reproducibility wins because it is the whole point of the metric.

**It cannot see work that is not Python**, and says so rather than reporting it
as cheap. Two sorts of different sizes both come back at 14 instructions;
`Separation.trustworthy` is false and the explanation refuses to name a winner.
That is the guard-counter rule in a new place — a metric that reads *cheap*
because the instrument could not see the work is the silent wrong answer this
project exists to refuse.

**The correction for a mapping result is close rather than exact.** `materialize`
drains a mapping through a different shape than a list, so the filler subtraction
does not match it exactly. The count is clamped at zero and the discrepancy is
small; a subject returning a mapping of large iterables would need its own
filler, and that is a change to make when a second case exists.

**A count is a search result, not a verified improvement**, and every
`Separation` says so. §12's workflow has two halves and this is the first one.
Skipping the second would mean shipping a change whose only evidence is a number
that says nothing about elapsed time.
