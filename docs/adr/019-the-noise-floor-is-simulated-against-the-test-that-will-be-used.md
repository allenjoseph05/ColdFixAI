# 019 — The noise floor is simulated against the test that will be used

**Status:** accepted
**Date:** 2026-08-04

## Context

S-1.7 requires a certification step that runs the baseline 20–30 times, computes
a coefficient of variation and a minimum detectable effect, and refuses to
proceed when the floor exceeds the effect the investigation is looking for.

The backlog calls it a novel contribution — no evolve-style framework certifies
its evaluator before optimizing against it — and says not to treat it as a
refinement. The reason is concrete: an optimizer that cannot detect a 5%
improvement will still return results from a search for 5% improvements. It
will return noise, confidently, in the shape of a finding. S-0.4 measured this
project's own floor at roughly 20 ms on a 350 ms endpoint, so a real 2% change
there is invisible however many repetitions are taken.

Three things had to be decided that the story does not settle.

## Decision

**The minimum detectable effect is simulated, not taken from a formula.** The
textbook expression assumes normally distributed samples. `stats.py` already
documents why timing distributions are not — bounded below by the fastest
possible execution, unbounded above, routinely bimodal on whether a cache was
hit — and that is precisely why the rank test was chosen over a t-test. Using
the parametric formula here would reintroduce, one layer down, the assumption
the test above it was picked to avoid.

Instead the baseline is resampled with replacement into a control group and a
treatment group, the treatment is scaled by a candidate effect, and the pair is
put through `rank_test()` — the same function the real comparison will call.
The effect is tightened by bisection until the detection rate reaches
`TARGET_POWER`. What comes back is a statement about the instrument that will
actually be used, on data shaped like the data it will actually see.

The returned value is always one whose power was *measured* at or above target,
never an interpolated midpoint, so the error is on the conservative side.

**A refusal raises, and carries the certification.** `NoiseFloorTooHighError`
holds the full artifact, following `ExecutionTimeoutError` carrying partial
output and `TimingError` carrying completed samples. Refusing by return value
would let a caller ignore it; refusing by an exception that discards the
evidence would make the refusal unloggable. A refusal is a result, and a result
that cannot be recorded may as well not have happened.

**`Certification` is a Pydantic model, and nothing here writes a log.** The
fourth acceptance criterion says the result is recorded in the experiment log.
That log is S-8.4, which depends on S-6.1 and S-5.7 and does not exist. Building
a second one now would guess at a schema S-8.4 already specifies — entries carry
hypothesis, primitive, design, measurement and verdict, none of which a
certification has. So S-1.7 guarantees only that the artifact is complete and
stably serializable, and S-8.4 owns the appending. Field order is the
serialization order and should not be shuffled: the append-only log's
prompt-cache prefix depends on unchanged entries rendering byte-identically.

**Two smaller readings.** `n` has a floor of 20 and **no ceiling** — the story's
"20–30" is guidance about what is sufficient, and refusing a caller who can
afford 60 samples would be refusing better evidence. And `alpha`, `power` and
`trials` are module constants rather than parameters, recorded on every
certification so a result can be read without knowing what was passed. A second
case can add the knob if one appears.

## Consequences

**Makes easy.** A refusal that says what to do: which effect was wanted, which
was achievable, the measured coefficient of variation, and the three things
that would change it. An investigation cannot silently start on an instrument
that cannot answer it.

**Makes hard.** Certification costs 20+ baseline runs plus roughly 2,600 rank
tests before any experiment begins. That is the intended price. It is also the
first place in the bench where a result depends on pseudo-random resampling, so
the seed is recorded and the estimate is reproducible from it.

**Rules out.** Certifying from measurements taken earlier. `certify()` takes a
callable for the same reason `compare()` does — a floor computed from numbers
measured on a machine that no longer exists certifies that machine.

## Provenance

The estimate is checked against its own definition from a different seed and
with twice the trials, which is what distinguishes "agrees with itself" from a
claim about the estimate. It is then checked end to end against `compare()`: an
effect four times the certified floor is found in five sessions out of five, and
an effect one sixth of it in at most one — both claims are probabilistic and
asserting either on a single run would be a flaky test dressed as a strict one.

Sabotage-verified. Making `certify()` never refuse fails two tests. Making the
floor optimistically small — the dangerous direction, since it certifies a
harness that cannot see anything — fails five, including the end-to-end check.

One measurement worth recording: a bare busy-wait loop certifies at a floor of
about 0.02%, which is true and made the first version of the end-to-end test
meaningless, because no effect was below the floor. Real noise had to be
injected deliberately to test the mechanism. A spin loop is a far better
instrument than any workload this project will meet.
