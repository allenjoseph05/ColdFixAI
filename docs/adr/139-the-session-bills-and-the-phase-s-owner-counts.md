# 139 — The session bills, and the phase's owner counts

**Status:** accepted
**Date:** 2026-08-23

## Context

S-7.14 built the Explorer loop: the driver `GroundingRun` was written for, which
had been constructed in `tests/explorer/test_run.py` and in no module under
`src/` since Epic 7 closed. `Agent.EXPLORER` had carried `attributed=False` since
the role index was written, with a note offering two explanations — *either the
loop that drives it is not built, or grounding's calls are billed to nobody.* It
was the first.

`00-BRIEF.md` §5 step 5 calls grounding *the step the project's viability turns
on*, and it was the one phase in the system with no agent behind it.

## Decisions

### 1. The loop sits above `ground_workload` and calls it

Three shapes were possible and the story asked for the question to be settled
rather than assumed. The loop **wraps** the sequence.

Six of ADR 009's nine stage predicates are facts about the *environment* — a
checkout, an importable framework, a configuration the framework accepts, a
database that answers, applied migrations, an enumerable route. The mechanical
sequence never establishes any of them and cannot; each of them has commands that
make it true. The other three — `auth`, `seed`, `work` — are established *by* the
sequence: `resolve_auth` mints the credential and `verify_work` seeds both scale
points and drives the route.

So the loop repairs the six, and then runs the sequence once. `REPAIRABLE` and
`ESTABLISHED_BY_THE_SEQUENCE` are asserted to partition `Stage`, so a tenth stage
cannot be dropped out of both and become one nobody works on and nobody settles.

**The `seed` half is the one that would have been wrong.** `seed` sits ahead of
`endpoint` in the ordinary stage order, so a repository that is migrated and
deliberately empty — which is exactly what Epic 7's composed subject is — reports
`seed` as its `first_incomplete`. A loop that read that number would spend its
budget filling a database `verify_work` fills correctly thirty seconds later, and
would then measure a scale nobody asked for. `blocking()` therefore returns the
first incomplete **repairable** stage, not the first incomplete one.

**A refusal from the sequence ends the run rather than starting a repair.** When
all six repairable predicates hold and `ground_workload` still says no, what it is
refusing is about the repository's *content* — no drivable route, no credential,
an endpoint that does no work — and no command at `connect` or `migrate` changes
any of those. `NotGroundableError`'s own docstring already makes that answer a
result rather than a fault.

### 2. The session bills; the phase's owner counts

**This is the defect the story found, and it had switched off one of the three
bounds AC 4 names.**

`Session.run` recorded a step against the budget for any phase whose cap is
counted in `StepUnit.STEP`. Exactly one phase is: grounding. And
`GroundingRun.attempt` records a step for grounding too. Nothing noticed, because
until this story nothing made a model call between two attempts.

With a loop, both fire on every turn, and the consequences are not symmetrical:

| | before | with a model call per turn |
|---|---|---|
| the 60-step cap | 60 turns | **30 turns** |
| the stall check | 15 unchanged reports | **never fires** |

The second is the serious one. `record_step` with `conclusion=None` **clears** the
run of repeats rather than extending it — deliberately, because a step that
concluded nothing is not the same conclusion twice. A model call carries no
conclusion, so one call between two attempts reset the counter every turn and
fifteen identical stage reports could never accumulate. S-7.10's second bound
would have been decoration in the first system that used it.

The fix is to delete the recording, not to make the two agree: two records
carrying the same digest halve both numbers, and both were chosen deliberately.
`conclusion` is gone from `Session.run` with it, because there was nowhere left
for it to go.

**The rule this leaves is uniform, and five of six phases already followed it.**
`run_investigation` records an experiment, `retry` records an attempt,
`patchaudit` and `verdict` and `testaudit` each record a round, and
`GroundingRun.attempt` records a step. A model call is not the unit of any phase.
S-5.4 predicted half of this in its own docstring — *a cap counted in calls would
halt investigation at a third of its intended budget* — and the fix then was to
count only where the unit was `STEP`, which left grounding as the one phase
counted twice by two owners.

The cap is still enforced: `authorize` reads the same counter before every
attempt, and a phase at its limit refuses the next call whoever advanced it there.

### 3. The command is the answer, and the harness runs it

`04-cost.md` §3 lists this step as *decide next action from a command result*
against the mechanical check **command exit code**. So the reply is argv — a
reply this cannot turn into a command has failed its own check before anything
runs it, and a free-text instruction would move that check to whoever had to
interpret it, which is where the check stops existing.

`Hands` is supplied by the caller and never held. Same construction as S-8.9's
`Executor`: the loop sequences and the harness acts. `03-agents.md` §2.5 puts the
denylist, the blocked egress and the workspace confinement on the container the
command runs in, and a loop holding its own executor would be a second place all
three have to exist.

**There is no `validate` parameter, and not for S-8.1's reason.** Hypothesis
generation may not cascade because §3 records that no validator exists. This step
*has* one — but the exit code is known only after the command runs, and S-5.6
validates the **reply**. A cascade is not refused on principle here; it has
nothing to check at the moment it would have to check it. What does use the exit
code is the loop, which feeds a failed command into the next question's history.

### 4. `Grounded` carries the verification and the reset proof

`GroundingRun.finish` is the only way a run succeeds — it observes the
verification, refuses a run with any stage incomplete, and emits through S-7.8's
gate. It takes the measurement and the proof, not the document they produced, and
`Grounded` carried only the document.

Two alternatives were worse. Re-running the sweep to obtain a `Verification`
already paid for is the waste this project flags in other people's code; reading
`grounded.emitted` and `grounded.progress.complete` directly is a **second
success path** past the one function S-7.10's AC 5 built to be the only one. So
the sequence carries both, and `finish` is called. The document is emitted twice,
identically: `emit` recomputes the verdict from the observations and holds no
state, so the second call is the check rather than a second workload.

### 5. `GroundingRun` keeps the report its last attempt was judged against

A driver has to know which stage to work on next. `attempt` returns one stage's
outcome, and the command it just ran may have moved a different one — so a driver
without `measured` evaluates all nine again with nothing having happened in
between, and then routes on a *different reading* from the one the bounds were
enforced against. Reusing it is the stricter behaviour rather than the cheaper
one. `observed` clears it, because evidence arriving is what makes a reading
stale.

### 6. The `ground` node calls the loop, so it has a production caller

Three things in this codebase are designed and unreachable — `ExperimentRef` (ADR
129), `gates_for` (ADR 138) and the playbook read — and the pattern is now
recognised well enough to check for it before calling a story done.

`Resources.ground` stays what a bound sequence always was; the node now calls
`explore(...)` by name with it. That required three new fields — `root`, `python`
and `hands` — and the session comes from `resources.sessions(_EXPLORER_PROMPT)`
rather than from inside the loop. **The session belongs to the node** for the
reason `Sessions` exists: it is keyed on the step's system prompt because that is
what `refuse_shared_session` compares, and a loop that made its own session would
be the one agent whose prefix nobody checked.

A repository that will not ground writes a `null_result` rather than raising.
S-7.11's acceptance is that the Explorer reports failure rather than claiming
success on empty data, and `00-BRIEF.md` §9 ships that as an answer — so the
report reaches the channel a person reads instead of unwinding the graph.

The node also writes `grounding_steps`. **S-13.5's learning curve had nothing to
read before**: while grounding was nine mechanical stages run once each, steps to
first runnable workload was the same number for every repository in the world.

### 7. `describe()` takes the index it describes

Closing the Explorer's gap made the *no call site names this agent* line
unreachable, and a line nobody can render is a line nobody has checked. The
parameter is `role_of`'s argument, arriving at the second function that needed
it. The next role added will spend a while unattributed and this is what will
show it.

## Consequences

- `Agent.EXPLORER` is attributed. `unattributed()` is now empty, and the test that
  asserted the gap — which said in as many words *when the grounding loop is built
  it will fail* — asserts its closure instead.
- The Explorer's stall check works for the first time. It is still the looser of
  the two per-run bounds at the default per-stage budget of eight, which
  `test_run.py` already records: a run spending everything on one stage is stopped
  at eight before fifteen unchanged reports can accumulate.
- `Session.run` no longer takes `conclusion`. Six call sites passed it; two in
  `src/` passed `None` and four were tests.
- S-13.5 is half unblocked. *Steps to ground* is now a variable and AC 1 has
  something to record. AC 4's ablation still measures zero by construction until
  S-13.7 lets a trusted playbook entry change an outcome.
