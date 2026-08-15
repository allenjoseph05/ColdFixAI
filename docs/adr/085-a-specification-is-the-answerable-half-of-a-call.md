# 085 — A specification is the answerable half of a call

**Status:** accepted
**Story:** S-8.2 — experiment design
**Date:** 2026-08-15

## Context

Three acceptance criteria — translate a hypothesis into a concrete experiment
specification; validate the specification against the chosen primitive's schema;
a mechanical step routed to the mid tier with cascade.

S-8.1 is the mirror of this story in every respect that matters. That one is
creative, frontier-only, and structurally unable to request a cascade because
nothing can check its answer. This one is mechanical, mid-tier, and cascaded
because AC 2 hands it the check.

Three things the acceptance criteria do not say had to be decided.

## Decision

### `04-cost.md` §3 gained a ninth row, because the loop it prices has nine steps

There is no *experiment design* row in §3's table. AC 3 requires a mechanical step
with a cascade, and neither is expressible without one: `Step.step_class` is
**derived** from that table (S-5.5, so that a call site cannot misdeclare), and
`cascade()` refuses any step type whose `mechanical_check` is `None` (S-5.6). A
step with no row has no derivable class and cannot be routed or cascaded at all.

The alternatives were both worse. Borrowing another row — `ABLATION_STUB` is the
closest — would let a row about one primitive decide the tier for a step that
serves all thirteen. Leaving the step unrouted would mean not implementing AC 3.

The row's check is what AC 2 states: *the specification validates against the
primitive's schema*. That is the same **kind** of check as the evidence chain's
already-present row — a schema, run over an artifact, with no judgement in it —
which is the argument for putting it on the cascade-safe side rather than a
convenience. §3 and `STEP_KINDS` were updated together, since the code says the
table is §3's and a table in code that §3 does not have is the drift this project
keeps catching in its own documents.

**The non-negotiable is re-asserted from this story rather than assumed.**
Editing §3's rows is precisely how the two *none exists* entries would stop being
*none exists* without anybody deciding to change them, so a test asserts that
hypothesis generation and attack design are still creative and still absent from
`cascadable()`.

### A primitive's schema is read from its function, and splits in two

The interesting discovery is what a primitive's parameters actually are.
`scale_volume` takes nine and a model can answer three:

```
seed, invoke, reset, extra_counters, clear_caches, process_identity   the harness
scales, distribution, counters                                        the design
```

So **a specification is not a call.** It is the answerable half of one, and the
partition falls out of annotations that were already written: a parameter whose
type a JSON document can express is the model's, and everything else belongs to
whoever grounded the workload. Nothing had to be added to any primitive to make
this work, which is the test of whether a partition is real rather than imposed —
and it holds across all thirteen registered instruments, checked by a sweep rather
than asserted on one.

Read from the callable rather than declared beside it, which is S-3.1's argument
for `Primitive.signature` one layer on: *two statements of one signature drift,
and the one that drifts would be the one the agent reads rather than the one that
is executed.* Here it is worse, because the reader is not a human.

**A mapping of numbers is never the design's to set.** The registry's one
`Mapping[str, float]` parameter is `bounds.headroom(metrics=...)`, which is the
shape of a *measurement* — and `CLAUDE.md` forbids an agent reporting one. A
schema that admitted it would defeat that non-negotiable through the front door,
with the numbers arriving inside a validated artifact having been typed by a
model.

**The schema checks shape, not sense, and that bound is stated in the code.**
`scales=[10, 100, 1000]` and `scales=[-4]` are both well-typed `Sequence[int]`;
`scale_volume` refuses the second on its own rules at the point of running.
Restating those rules here would be the second statement this module exists to
avoid. So *validated against the schema* means **the primitive will accept the
call** and never **the experiment will produce a measurement** — a distinction
somebody would otherwise quote without.

### A retry has to be able to differ from the attempt it retries

§3's escalation policy is *2 cheap attempts, then strong*, and it silently assumes
the second attempt is a second answer. At temperature 0 it is not: same model,
same prompt, same sampling. Two identical calls are one call and a wasted budget
authorization — and against S-0.7b's replaying client they are literally the same
digest, so the cascade would replay the recording that had already been rejected.

Two ways to fix it. Raise the temperature, which buys variation by rolling dice
on a step that has a correct answer. Or feed the rejection back, which makes the
second attempt a model being told what was wrong. **A retry told what was wrong
is a correction; a retry at a higher temperature is a dice roll**, so the
rejection is appended to the question and the temperature stays at 0.0.

The rejections go **last**, after everything else in the question. `04-cost.md`
§4 puts the stable prefix first and the varying part last, and the rejection is
the only part that differs between attempts — anywhere else would invalidate the
cached prefix on every retry, on the one step in the system designed to make three
calls.

This also decides which failures are retryable. A reply that is not JSON, or
whose arguments the schema rejects, is a **wrong answer**: it fails the mechanical
check, S-5.6 tries again, and the step recovers. A refusal or a truncation is not
a wrong design but an **absent** one — there is nothing to correct, and feeding
*your previous answer was rejected because the model declined* back to a model is
noise. Those raise.

Because a cascade discards the results it rejected, `NoDearerTierError` reports
the step type and the model and **not what was wrong** — which for this step is
the whole diagnosis. `UndesignableError` carries every attempt's rejection, since
*the design was invalid* is not actionable and *it set `scales` to a string three
times* is.

### The validator is not replaceable, which is S-8.1's absence inverted

S-8.1 has no `validate` parameter so that no caller can request the cascade
`CLAUDE.md` forbids. This story cascades, so the danger runs the other way: a
caller-supplied check returning `True` would make the cascade decorative and let
an unrunnable specification through wearing a validated artifact's clothes. The
validator is the schema's, `design()` has nowhere to pass another, and a test
asserts it by inspection. Fourth instance of this construction, after S-7.8's
missing `force`, S-7.10's single exit and S-8.1's own.

### The primitive is not asked for a second time

It came with the hypothesis and S-8.1 already validated it against S-3.1's
`Selection`. Asking again would create two answers to one question with no rule
for which wins — the shape S-7.12 refused when it declined to carry an override
flag beside an override value. A design assembled for some other instrument fails
schema validation with the parameter names in the message, which is a better
diagnosis than a disagreement would have been.

It **is** re-resolved through the selection, though. A `Hypothesis` can be rebuilt
from a log written in an earlier run and a selection is a snapshot of *this* one,
so an instrument that was offered then and withheld now is refused here, where the
withholding reason is still attached.

## Consequences

A specification carries only what a model can answer. Whoever executes it still
owes the workload, the reset and the session, and `PrimitiveSchema.bound` is the
list of what is owed — deliberately exposed rather than merely excluded, because a
specification that looks complete while missing every binding is the shape Epic
7's composition check found six times.

`ExperimentSpec.render()` is canonical, so two runs that designed the same
experiment produce the same string and S-8.4's digest agrees about them.

## Sabotage

Twenty-seven properties, all caught — but **two are worth recording, and one was a
defect in this story's own claim.**

*The mapping exclusion survived its first sabotage, and the decoy was at fault.*
Adding `Mapping` to the specifiable origins changed nothing, because a mapping has
two type arguments and the element check rejects it for that instead. The property
is real — origin and arity relaxed together does fail three tests — but it is
guarded twice over and neither guard is the one a reader would name. Sixth
consecutive story to record a fixture that could not discriminate, and the
sharpest form of the lesson so far: **a rule that can only be broken by two
simultaneous edits cannot be tested by one.**

*`strict=True` was guarding nothing, and the docstring said it was.* Turning
pydantic's strict mode off changed no behaviour, because `JSONValue` covers every
type JSON has and there is therefore nothing for a coercion to reach for. What
actually keeps a boolean argument a boolean is `bool` being **in** the union;
removing it is the sabotage that fails. The setting stays as the right stance for
an artifact whose fields are exactly what a reply carried, but the claim was
corrected — a guarantee resting on a setting that was not providing it is worse
than no claim, because it is the one nobody re-checks.
