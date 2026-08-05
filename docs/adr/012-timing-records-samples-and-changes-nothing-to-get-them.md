# 012 — `time()` records samples and changes nothing to get them

**Status:** accepted
**Date:** 2026-08-03

## Context

S-1.2 specifies three things `time()` must do and one thing it must not: no
automatic warmup discard, because Barrett et al. (2017) found at most 43.5% of
VM/benchmark pairs reach a steady state at all.

Writing it surfaced that warmup is not the only such decision, only the one the
backlog happened to name. The reference implementation of "time something N
times" in the standard library is `timeit`, and it also:

- disables the garbage collector for the duration of the timing,
- runs an inner loop and reports the total, so one reported number is the sum of
  many calls,
- offers `repeat()` returning the **minimum**, on the reasoning that anything
  above it is noise.

Each is defensible for microbenchmarking a pure function. Each is wrong here,
and wrongly in the same direction as the warmup discard: it makes a decision
about which part of the observed behaviour counts, silently, on the caller's
behalf, at the exact layer this project has said decides nothing.

The third one is the sharpest. Taking the minimum assumes the true cost is the
fastest observation and everything else is contamination. Under that assumption
a workload that is fast four times in five and pathological on the fifth reports
as fast — and "pathological one time in five" is a finding, not noise.

## Decision

`time(fn, repetitions)` returns one `Sample` per call, in order, and takes no
position on any of them.

Specifically:

- **`perf_counter`, not `process_time`.** Wall clock, including time spent
  blocked. A workload waiting on a database is doing the thing we are measuring.
- **One sample is one call.** No inner loop, no division. Per-sample variance is
  what the rank test in S-1.5 consumes and what makes a bimodal distribution
  visible; a batched mean destroys both.
- **The garbage collector is left alone.** Disabling it would make a patch that
  increases allocation pressure appear free, and allocation pressure is a defect
  class this tool exists to find.
- **No summary statistics on the return type.** No `mean`, no `min`. Summarising
  is `stats()` (S-1.5).
- **The `fn` return value is not retained.** Holding `repetitions` results alive
  is memory the unmeasured program would not hold, which changes collection
  behaviour partway through a run and charges it to the later samples.

**Process provenance is declared by the caller and scoped to the run.**
`ProcessState.FRESH` means *no earlier sample in this run executed in this
process*. It does not claim a newly started interpreter, a cold cache, or a
quiet machine. `time()` cannot observe whether `fn` spawned a process — `fn` is
an opaque callable — so `fresh_process_per_sample` is the caller stating what it
built, and it defaults to the conservative reading.

## Consequences

**Makes easy.** Every analysis downstream sees the raw run, so a question nobody
anticipated at this layer can still be asked of the data: a rising trend across
a run, a bimodal split, a slow first call that is warmup on one workload and the
real cost on another. None of that survives a layer that averages.

**Makes hard.** Callers must handle noise themselves, and the numbers are
noisier than `timeit`'s by construction — a run with the collector live and no
batching has visible outliers in it. That is deliberate, but it means `time()`
alone is not enough to compare two variants. S-1.6 (interleaved sessions) and
S-1.7 (noise floor certification) exist because of this and are not optional
refinements; without them a caller comparing two means of raw samples will draw
false conclusions, and Laaber et al. showed that is the common outcome.

Sub-millisecond callables also measure poorly, since one call may approach timer
granularity. The `timeit` answer is batching, which is ruled out above. The
answer here is that a caller wanting to time something that small should time a
loop it wrote itself, and own the fact that it is now measuring a loop.

**Rules out.** Reusing `timeit` as the implementation. Also any future
`min`-based comparison, and any change that makes the runtime quieter during
measurement — no collector control, no priority pinning, no allocator tuning.
The tool measures the runtime the target actually has.

## Provenance

`docs/10-BACKLOG.md`, S-1.2 note (Barrett et al. 2017, steady state). The
extension from warmup to the rest of `timeit`'s defaults was found while
implementing it, and the minimum-taking case has no citation behind it beyond
the project's own rule that null and awkward results are valid output.
