# 123 — Six enforcements and no index

**Status:** accepted
**Date:** 2026-08-20

## Context

A review of the system against the orchestrator-worker pattern asked four
questions: is there a coordinator agent, do specialists run in parallel, do they
avoid talking to each other, and is everything scoped correctly.

Three of the four had good answers. The fourth exposed something real: **which
agent may see what is enforced in six separate places and written down in none.**

`cost/accounting.py` enumerates five agents, but that enum exists for cost
accounting — it answers *who spent this*. It is not a capability declaration and
never claimed to be.

## Decisions

### 1. An index, not a seventh enforcement

The six existing enforcements are structural and none is persuadable, which is the
property that matters. Adding a runtime capability check would be a second answer
to questions already answered, and the two would disagree the first time one moved
— the failure this project has found five times at epic joins.

So `agents/roles.py` **declares and verifies**. The enforcement stays where it is;
the tests beside the index check the description against the code.

### 2. What the tests actually catch

- **A prompt with no owner.** Every `SYSTEM`/`_SYSTEM` in `src/` is found by
  parsing, not importing — a new prompt in a module nobody imports is precisely the
  one that would slip through — and must be claimed by exactly one role.
  `refuse_shared_session` compares against the prompt, so an unclaimed one is a
  call nobody can attribute.
- **A withheld field reappearing.** The Adversary's `withheld` names are checked
  against `Candidate`'s actual fields, and the Surgeon's against
  `FalsificationTest`'s. A boundary nothing can verify is a comment.
- **An agent added to the enum with no boundary written for it.**
- **A prompt with two owners** — two agents on the same side of a boundary is not a
  boundary.

### 3. An agent has a prompt per step, not per role

The Diagnostician owns three and the Adversary owns three. `refuse_shared_session`
keys on the prompt, so the session boundary is finer than the role — a hypothesis
cannot be justified by the framing that produced it, because propose, design and
interpret are three sessions.

The index records this rather than flattening it, because flattening would make the
boundary look coarser than it is.

### 4. The unattributed role is a field, not an omission

`Agent.EXPLORER` appears in no call site in `src/`. `explorer/run.py` authorizes
and records `Phase.GROUND` against the budget — so the spend is bounded — but
nothing attributes a model call to the agent that is supposed to make them.

`00-BRIEF.md` §5 step 5 calls grounding the step the project's viability turns on.
Either the loop that drives it was never built, or its calls are billed to nobody.

`Role.attributed` records it and a test asserts it, so the gap fails loudly the day
it is closed and the index has to be corrected. An index that quietly listed five
working roles would have hidden the one finding this exercise produced.

## Consequences

**The review's other three answers stand, and two are deliberate deviations.**

There is no coordinator *agent*: `orchestrator/graph.py` makes zero model calls,
and every decision it makes is budget arithmetic or a schema check. A reasoning
coordinator would reintroduce a persuadable gate, which is what `08-audit.md` spent
its length removing.

Nothing runs in parallel, and that is contraindicated rather than merely absent.
`concurrency` is a recorded *condition* on every exclusion and
`primitives/isolation.py` exists to measure what happens when two things run at
once. Running measurements concurrently on one host would not be a speedup; it
would be a source of false findings.

**A guard no test can reach is a guard nobody has checked**, so `role_of` takes the
index as a parameter. Closed over the module-level one, its refusal could only fire
for an agent that does not exist.

**The first version of the attribution test failed on the index itself**, because
`roles.py` names `Agent.EXPLORER` in order to declare it. Naming a role is not
attributing a call to it, and counting the declaration would make every role look
attributed the moment it was written down. Both that file and `accounting.py` are
exempt.
