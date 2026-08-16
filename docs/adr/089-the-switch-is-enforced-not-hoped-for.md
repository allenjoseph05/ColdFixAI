# 089 — The switch is enforced, not hoped for

**Status:** accepted
**Story:** S-8.7 — instrument switching (the thesis behaviour)
**Date:** 2026-08-16

## Context

`00-BRIEF.md` §5 step 7: *the agent must switch instruments when the first comes
back flat… **this is the demo that justifies the whole architecture.*** §1 states
the claim it demonstrates — the fourteen methods are mechanizable, and *choosing
which one applies to a given program* is the part the fault-localization
literature names as requiring expertise.

Three acceptance criteria — on a rejected hypothesis the next must select a
different primitive where the evidence supports it; demonstrated end to end on a
repo where query count is flat; and the switch and its rationale in the log.

## Decision

### AC 1 is a code-enforced property, not an expectation of the model

*The next hypothesis **must** select a different primitive* is a rule about the
system, and `CLAUDE.md`'s hard-enforcement table is explicit: a rule that must
hold regardless of what an agent decides lives in code, not in a prompt.

So a hypothesis re-proposing an instrument already rejected under unchanged
conditions is refused, and the refusal is fed back into a fresh `generate` call.
After `RETRIES_PER_HYPOTHESIS` repeats the loop raises `NoNewInstrumentError` —
which is written as a **result**, not a fault: the honest reading is that this
subject's applicable experiments are exhausted, and `00-BRIEF.md` §9 ships null
results as answers.

**Re-asking is not cascading, and the distinction is the non-negotiable.** S-8.1
must never cascade, because no deterministic validator exists for a hypothesis.
Re-asking calls `generate` again at the same temperature on the same tier with a
longer exclusion list; no validator is supplied, no model changes, and the
routing stays S-5.5's. What makes it legitimate is that the thing corrected is
not the hypothesis's *quality* — which nothing can judge — but a fact the agent
can simply be told: this instrument has already answered. A test asserts the word
`validate` appears nowhere in `propose`.

### "Where the evidence supports it" is S-8.5's rule, not a new one

The loop asks the exclusion register which instruments are settled *under the
conditions now in force*, rather than keeping its own list. So an instrument
becomes re-proposable the moment a condition moves — a reseed to a skewed
fixture, a higher concurrency, a wider scale — and S-8.8's reseed will reopen it
without this module knowing that reseeding exists. One answer to *has this been
settled*, in the story that owns the question.

### AC 3 required a field on the record

`Experiment` stored the primitive but not why it was chosen. The primitive alone
shows *that* the instrument changed; **the thesis claim is about the choosing**,
so `rationale` — which S-8.1 already produces and was discarding — is now a
required field. Required rather than defaulted, on S-5.4's argument: a default
would make AC 3 hold only for the callers that remembered.

`switches()` is a **view, not a record**: a switch is a property of two adjacent
entries, and storing it would be a second statement of what the log already says
— the shape S-8.5 refused for `invalidated_if` and S-8.6 for `confidence`.
`describe_switches()` names the provoking verdict too, because *switched after a
rejection* and *switched after a confirmation* are different behaviours and only
the first is being claimed.

### What the demo proves, and what it does not

This is the story where over-claiming would be easiest, so the boundary is
written into the module and the test file rather than left to a reader.

**The measurements are real.** `scaling.volume` and `ablation.stub` execute
against a planted defect and the numbers come back from the harness. The subject
is `tests/fixtures/planted/rendering.py`, which is not a new defect but
`render_with_expensive_downstream` expressed as a collaborator so an instrument
can interpose on it — the shape S-3.4's docstring says it needs and no real
subject had provided. Measured: two queries at every scale, and stubbing the
renderer removes essentially all of the wall time.

**The model calls are replayed**, because `CLAUDE.md` forbids a test hitting the
API. So the test proves the rejection propagates, the harness refuses a repeat,
and the log records the switch and its reason. It does **not** prove a model
would choose to switch unprompted.

`run_investigation` takes the client as a parameter for exactly that reason: the
video the backlog asks for runs this same function against `AnthropicClient`, not
a second implementation of it. A demo of a second implementation would be a demo
of the second implementation.

### The measurement reaching the model is counts and a rounded share

A replayed call is found by hashing its prompt, and a prompt carrying
`8.2447281639…` differs on every run — so an end-to-end replay over real timings
is impossible in principle. Counts reproduce to the integer, which ADR 052
already makes the reason counts are what raise a flag, so the executor reports
those; the ablation share is rounded to two decimals, which is far coarser than
an effect that removes ~100% of the work, and the raw timings travel in `detail`
where they are retrievable and not hashed.

### The loop holds no budget

`max_steps` is a loop guard, not a cap: without some bound this is a `while True`
around a paid API. S-8.9 owns the budget and the progress check, and inventing
them here would guess at a shape that story designs — the fifth time this project
has declined that guess.

Nor does the loop assemble an evidence chain. S-8.6 requires a symptom, a
mechanism, a site and the implicated files, and none of those is something the
loop measured; a loop that manufactured them to satisfy a constructor would be
inventing precisely the parts of a finding that are hardest to check.
`confirming_links` hands over the half it owns.

## Sabotage

**The first pass was worthless and reported a clean sweep of nineteen.** The
runner passed `--timeout=120` to a pytest without `pytest-timeout` installed, so
every run exited non-zero on an argument error and every sabotage read as caught.
The tell was in the output the whole time — every "caught" line had an empty
`by:` — and it is the same class of fault Epic 5's composition run hit with
`git checkout` and Epic 6's with a marker filter, which is now three.

The runner now **fails loudly on a non-zero exit with no parsed failure**, and
verifies the baseline both before and after.

The real pass: nineteen properties, all caught — after four survived, and all
four were untested properties rather than weak code.

- *The rationale dropped on its way into the log.* AC 3's own test built its log
  by hand and the thesis run never read the field back, so the criterion was
  asserted everywhere except through the loop that has to satisfy it.
- *The loop treating a narrowed verdict as confirmed*, and *the loop unbounded*.
  Neither path is reachable with the planted defect, which confirms on its second
  step — so both needed a synthetic run to exercise at all.
- *`switches()` reporting every consecutive pair.* The fixture had exactly two
  experiments with two different primitives, so *every pair* and *every changed
  pair* were the same list. Three experiments where two share an instrument tell
  the rules apart. The sixth instance of a fixture that could not discriminate.
