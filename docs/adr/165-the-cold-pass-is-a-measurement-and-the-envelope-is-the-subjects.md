# 165 — The cold pass is a measurement, and the envelope is the subject's

**Status:** accepted
**Date:** 2026-08-28

## Context

S-17.14 produces `Resources.measure`, the last of the six subject-facing fields.
`cheating.detect` and `trades.audit_trades` both measure nothing themselves —
`CLAUDE.md` puts the measuring in the harness — so both are handed a
`Measurements`, and nothing filled it.

Building one found two things wrong with what it had to read from, and both are
the same shape as S-17.5: an instrument that runs, produces numbers, and is
measuring the wrong thing.

## 1. The cold pass had a duration and no counters

`Reading` holds `first` — the pass that paid for whatever the process had not yet
warmed — and `repeated`, *"the passes after it in the same process, which is what
makes the pair able to see state carried across runs"*. Each is a full
`Mapping[str, float]`.

`Drive` carried `warmup_seconds`, a **duration only**, plus the *last* pass's
`queries`, `response_bytes` and `status`. And in `_DRIVE_SOURCE` the warm-up ran
**outside** `CaptureQueriesContext`, so the cold pass's query count was never
measured at all.

That matters concretely. `_cached_state` compares the patch's warm-up excess on
`metrics.cost` — whichever metric the patch claims to reduce — and for the defect
class this system targets that is a **count**. So the check the `Reading` shape
exists for could not run on the metric that matters.

**The driver now measures every pass, warm-up included**, and `Drive` carries
`passes` and `warm_pass`. The aggregates screening reads are computed from the
same passes and are unchanged, which is what makes the change provably safe: the
whole explorer suite — 481 tests including the slow real-Django ones — passes
untouched.

**One injected program rather than a second driver.** It is the only thing that
knows Django's capture API, and a second would be two places that must agree about
what a repeat is.

## 2. `envelope()` measures the harness

`primitives.envelope._read` takes `resource.getrusage(RUSAGE_SELF)`,
`sys.getallocatedblocks()`, `threading.active_count()` and this process's
descriptor directory. Every one is about the interpreter that calls it.

Wrapped around a containerised drive it reports what the **harness** did while it
waited. `audit_trades` compares `envelope_before` against `envelope_after` to
catch *queries down while rows explode*; taken that way both samples describe the
same idle interpreter, every difference is noise, and every trade reads as absent.

**So the subject samples itself** either side of its own drive and reports the
levels back, under `primitives.envelope`'s own metric names — a second spelling
would leave every metric silently unwatched. This is S-17.6's rule landing a third
time, in a place with no vantage type to catch it.

`sample_of` fills every metric `ENVELOPE` names: a number where the subject could
report it, `None` with an `Availability` where it could not. An absent key would
read as *not watched*; a `None` with a reason reads as *looked for, and this
platform cannot say*, which is what lets `trades` report a guard it could not
evaluate rather than one that passed.

## The safety property of the measurer itself

**The revision decides the session and nothing else does.** `ORIGINAL` runs on the
diagnostic session, `PATCHED` on the candidate. A `Measure` that read one session
twice returns identical numbers for both, and `cheating._read` cannot catch it —
that function checks the reading is *tagged* with what was asked for, and the tags
are whatever the measurer puts on them. Every class would come back
`NOT_DETECTED` and the patch would ship on a measurement that never distinguished
it from the original.

The diagnostic session is bound at construction and the candidate arrives per
patch, which is the `Measurer` protocol and is the right split: the original
revision is the same for every patch of one finding, while the candidate is what
changes.

**`metrics`, `shape`, `alternatives` and `claim` are supplied, not measured.** Only
the adapter knows what its counters are called; only the repair knows what the
patch promised. Deriving either here would be this module deciding what the patch
was for.

## Consequences

**All six subject-facing fields now have producers**: `hands`, `ground`, `bind`,
`executor`, `probe`, `measure`. Nothing yet assembles a whole `Resources` — that
is the next story, and S-17.1 is still not a run.

**`Drive` grew four fields and no caller had to change.** `passes`, `warm_pass`,
`envelope_before` and `envelope_after` all default to empty, so a `Drive` an
adapter built from aggregates alone is still valid — `adapters/flask.py` builds one
and is untouched. `reading_of` refuses a drive with no cold pass rather than
falling back to the repeats, because a reading built from those alone answers the
cached-state question with the warm-up folded into the thing it is supposed to be
measured against.
