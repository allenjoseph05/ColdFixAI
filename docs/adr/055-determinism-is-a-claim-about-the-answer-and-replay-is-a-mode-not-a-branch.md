# 055 — Determinism is a claim about the answer, and replay is a mode not a branch

**Status:** accepted
**Story:** S-5.2 — replay mode
**Date:** 2026-08-09

## Context

S-5.2 asks for three things:

- a recorded investigation can be replayed to debug downstream agents;
- replay is byte-identical to the original for deterministic experiments;
- non-deterministic experiments are marked and re-run rather than replayed.

The third is the one that decides the design, because the obvious reading of it
deletes S-5.1. Every measurement `measure_once` takes carries `seconds`,
`cpu_seconds` and `blocked_seconds`, and no duration ever repeats. If
*deterministic* means the artifact reproduces byte for byte, then nothing in this
system is deterministic, nothing may ever be replayed, and the cache built in the
previous story can never legitimately hit.

## Decision 1 — determinism is about the answer, is declared, and defaults to sampled

`Determinism` distinguishes whether the thing an experiment is *for* reproduces.

A screening sweep concludes from counts. Counts reproduce to the integer, and
ADR 052 already forbids a duration from raising a flag on its own — so the sweep's
answer is stable even though its artifact is not. An interleaved timing
comparison (S-1.6), a load curve (S-3.12) and a fuzzing campaign (S-3.17)
conclude from a distribution, and a second run answers differently.

**Nothing here can check the claim.** Only the primitive knows what its own answer
rests on, so the declaration lives at the call site nearest that knowledge — the
same position `experiment_spec` is in, and for the same reason.

Two things follow, and both are the conservative choice:

- **The default is `SAMPLED`.** An experiment nobody classified is one nobody has
  thought about. Defaulting the other way would spend the very first unconsidered
  call site on a silently stale answer, with no signal anywhere that it happened.
  This default was not free: adopting it broke four of S-5.1's own tests, which is
  what a default with teeth looks like.
- **A replay while recording needs the claim from both sides** — the one made now
  and the one the recording was made under — and the stricter wins. A caller who
  has decided their experiment is deterministic must not thereby promote somebody
  else's sample; a caller who has said they need a fresh sample must not be
  overruled by a recording that was taken as deterministic.

## Decision 2 — `RECORD` and `REPLAY` treat a sampled experiment oppositely

While recording, a sampled experiment is re-run and its recording refreshed.
While replaying, it is played back.

That looks inconsistent and is not: the two modes are doing opposite jobs. A
fresh investigation must never treat one afternoon's sample as a standing fact. A
debugging session must never let the session being debugged stop being the session
that was recorded — an experiment that re-ran mid-replay would change the input
the agent under test is being debugged against, which is the one thing replay
exists to hold still.

What carries the difference outward is the `Recall`. A replayed sample says, in
the sentence a report would quote, that a fresh run would answer differently and
that this is a record of what happened rather than a current measurement. A
replayed deterministic experiment deliberately does **not** carry that sentence —
Epic 4's composition check found that a caveat attached to everything is a caveat
nobody reads.

## Decision 3 — three modes on one object, because the alternative is a branch in every caller

`ReplayMode` is `RECORD`, `REPLAY` or `OFF`, and `run` takes the same shape in all
three. Nothing above the cache branches, which is what makes replay a way of
debugging *the agent that calls it* rather than a second route through it: the
agent runs unchanged and every experiment it asks for is answered from disk.

`OFF` exists because S-15.1's agreement study has *runs diagnosis N times with the
cache disabled* as an acceptance criterion — agreement between ten replays of one
recording is 100% and means nothing. Without a mode, that study writes
`if use_cache:` around every experiment, which is exactly the branch living in no
module that Epic 4's composition check found every caller reimplementing.

Two refusals make the modes structural rather than conventional:

- **`replay()` has no `compute` parameter.** A debugging session has no subject —
  no checkout, no container, no database — so a caller able to pass one is a caller
  who still had to build all of it. The unsafe state has no argument to arrive
  through.
- **Only a recording cache writes.** `record` refuses in `REPLAY` and `OFF` rather
  than being merely avoided by `run`. A replay that wrote back what it played back
  would stamp every recording with today's date, so the act of debugging a run
  would destroy the record of when the run happened; and a disabled cache that
  recorded would leave a study's own runs in the store for whatever ran next. This
  one was found by sabotage: it was the single property of fifteen that no test
  caught, because it held only as a consequence of one early return.

**A missing recording is refused, and the refusal lists the store.** Falling back
to running it is not a slower answer — there is no subject — it is a crash further
down that nobody connects to the cache. And a bare "not found" is close to useless,
because the four things that could be wrong look identical from the call site: a
different working tree, a different fixture, a spec spelled differently, or an
experiment the recorded session never ran. Listing what is held separates them.

## Consequences

**There is no artifact here describing an investigation.** The ordered account of
what was asked and why — hypothesis, primitive, design, measurement, verdict — is
S-8.4's append-only experiment log. Inventing a second one would be guessing at a
schema that story already specifies, which is the argument S-1.7 recorded for
leaving `Certification` unlogged and confirmed with the user before proceeding. So
an investigation is replayed by re-running the agent against a store that answers
instead of measuring, and what this module adds is `recordings()` — the inventory,
answering the question replay actually raises: *what is on this disk*.

**AC 2 is asserted two ways.** A screen replayed serializes to the same bytes as
the screen recorded, on the real artifact rather than a hand-built one; and the
codec is a fixed point, so a value read out and written back produces the same
JSON. The second matters because a codec lossy by a little would pass a single
round trip and drift over a session replayed from a session replayed from a run.

**S-5.1's AC-4 test was passing for the wrong reason, and this story found it.**
It recorded a screen, poisoned `socket` and `subprocess`, replayed, and compared
plans — but the planted fixture needs neither a socket nor a subprocess, so a
*live* second screen passed it just as well. Poisoning the world proves nothing
reached for it; only the hit count proves nothing needed to, and that assertion is
now there. The same shape as the three earlier occasions this project has recorded:
a sabotage that passes usually means the test is weak, not the code.

**A replay-mode result must still not become a finding on its own.** The `Recall`
carries the mark and the date, and that is as far as this layer can go. The gate
that refuses a finding whose evidence is a replayed sample belongs with the
finding schema in E9, where the evidence chain is validated.
