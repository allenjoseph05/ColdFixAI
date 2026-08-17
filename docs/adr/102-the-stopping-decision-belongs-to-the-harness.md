# 102 — The stopping decision belongs to the harness

**Status:** accepted
**Story:** S-9.9 — sufficiency and the null result
**Date:** 2026-08-17

## Context

ADR 094 added this story because S-0.8 measured the agent choosing *no finding,
stop* **0 times in 60** — including on the scenario where stopping was the only
correct answer — and concluded the stopping decision *probably cannot be the
agent's own*. S-8.9's cap bounds the damage without deciding sufficiency, and the
`PartialChain` it emits is the one artifact **nothing else in Epic 9 can audit**,
because every other story assumes a finding exists.

The question this story answers: is *nothing was found* a result, or a run that
stopped too early?

## Decisions

### 1. No model is asked

S-0.8 measured a model, given clean evidence and a correct diagnosis, declining
to stop sixty times out of sixty. Routing that same question through a second
model and hoping a different frame saves it is the same question asked of the
same kind of thing. ADR 094's *cannot be the agent's own* reads honestly as
*cannot be a model's*, and F6 already supplies the rule: **what counts as enough
is decided by the harness, because a self-judged criterion is one the judge is
incentivised to claim.**

Every input is a fact the run already recorded — why it stopped, what it ruled
out, and whether those exclusions were adequately conditioned — so `CLAUDE.md`'s
*do not add a model call where a function would do* settles it. **Six of Epic 9's
eight audit stories need no model.**

### 2. Why the run stopped is the first-order signal, and `CAP` is never enough

- `INSTRUMENTS` — ran out of **questions**. Nothing remains to try.
- `STALL` — S-5.4 has already concluded *more steps of the same kind will spend
  budget without changing the answer*, which is a sufficiency judgement the
  budget module makes before this one does.
- `CAP` — ran out of **money**, with something still being proposed. A negative
  from an interrupted search is not a negative.

### 3. Sufficiency and `Stopped.disposition` answer different questions

§7.2 gives the cap `PARTIAL` (emit the chain) and the other two `ESCALATE`. That
is what the **run** does next. Whether the negative is **believable** is a
different question, and the `CAP` stop is at once the one that ships a partial
chain and the one whose negative is worth least. The two answers deliberately
diverge; folding sufficiency into the disposition would answer one with the
other.

### 4. A result with no content is not a result

`PartialChain` says the exclusions **are** the result and allows the tuple to be
empty — correctly, since forty narrowings that never rejected have still learned
something. But *learned something* and *established a trustworthy negative* are
different claims, and `00-BRIEF.md` §9 ships the second. A run that closed no
doors has ruled nothing out.

The rule over several exclusions is `any`, not `all`: a negative resting on four
exclusions is only as good as its weakest, because the hypothesis the narrow one
failed to rule out is still live.

### 5. There is no minimum experiment count

Any floor would be a guess, and S-9.4's precedent is that a threshold is derived
or it does not belong. A subject supporting one applicable instrument that came
back rejected has answered the question in one experiment. The exclusion rule
does the work without inventing a number.

### 6. AC 1 needed no new machinery

*Were the exclusions established under adequate conditions* is S-9.2's question
word for word, and a `PartialChain` carries the same `Exclusion` type it already
attacks. A second conditions-checker here would be two modules holding two copies
of one argument — which is what S-9.3 recorded is *not* happening only because
both consult a single proof.

### 7. AC 3 is a property of the type, not a branch

The verdict is about a `Subject.PARTIAL_CHAIN`, and S-9.8 refuses `sound`,
`unsound` and `unrepresentative` about one — those three presuppose a claimed
cause. So the only constructible answers are `negative_sound`, which routes to
`REPORT` without reading the budget at all, and `inconclusive`, which escalates.
**Neither can reach investigate however much budget remains**, and there is no
exception anybody could add.

Escalation is right for the insufficient case for a reason rather than by
default: a run stopped by the cap has no experiments left to answer with, and one
stopped by the stall has just been told more of the same will not change the
answer. In both, *run more experiments* is unavailable — the lever ADR 094 says
an audit must not reach for.

## An undeclared dependency, recorded rather than assumed

`10-BACKLOG.md` lists S-9.9 as depending on S-9.1 and S-8.9. It also depends on
**S-9.8**, because ADR 094 put `negative_sound` in that story's vocabulary, and
on **S-9.2**, whose exclusion audit is AC 1. Both were written before ADR 094
rearranged the epic. Noted here rather than silently satisfied.

## Consequences

**AC 4 runs against the real loop.** A `run_investigation` with one instrument,
one rejection and three proposals of the instrument that already answered ends in
`Stopped.INSTRUMENTS` with **39 of 40 experiments still available** — the agent
had not decided to stop, it had just asked for a fourth experiment three times.
The audit sends it to `REPORT`. Only the exclusion's conditions are widened for
that assertion, because the thesis fixture is uniform-only and serial-only by
construction; **the same run under the conditions it actually had is
`inconclusive`**, which is the honest reading and still does not go back to
investigate.

**The bound is in the artifact.** `negative_sound` means *this run's exclusions
hold and it ran out of questions* — never *there is no performance problem here*.
This audit sees the hypotheses the investigation attempted and cannot see the
ones nobody thought of; that question is S-9.5's, and a `negative_sound` is only
as strong as the attacks that ran beside it.

**Sabotage: 22 properties, all caught, zero skipped, after one survived and one
was skipped.** The survivor is the one worth recording: hardcoding the stop
reason in the artifact left **every verdict correct and every report wrong** — a
cap-stopped run whose audit says *every applicable instrument had already
answered* tells a human the search finished when it was cut off. Nothing was
asserting that the audit's account of the run agreed with the run. The skip was a
sabotage pattern that stopped matching after a reformat, surfaced only because
S-9.7 made the runner print the skip count.
