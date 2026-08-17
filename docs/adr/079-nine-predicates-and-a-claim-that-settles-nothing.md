# 079 — Nine predicates, and a claim that settles nothing

**Status:** accepted
**Story:** S-7.11 — stage predicates (**SAFETY**)
**Date:** 2026-08-14

## Context

ADR 009 already made the decision this story implements: grounding is a fixed
nine-stage pipeline, each stage carries a predicate the harness evaluates, and
the predicates are framework-scoped and resolved through S-7.1's fingerprint.

The *Why* is the gap it names: without a definition of done per stage, **an agent
stuck at stage four and an agent progressing normally are indistinguishable until
the global cap fires.** S-7.8 computes the last stage's predicate in the harness
precisely because the agent is incentivized to claim success; the other eight had
nothing.

What this story adds is the code, and three decisions the ADR did not have to
make.

## Decision

### An agent may claim; a claim settles nothing

`claim(stage, …)` exists and is meant to be called. An agent reporting *I think I
have configured it* is the agent doing its job, and refusing to hear it would
only push the claim into prose nobody checks. What comes back is **this stage's
predicate, measured now** — and a claim about a stage whose predicate does not
hold is refused, with what was measured and what done would have meant.

That pairing is the point. The claim is an *input to a check*, never an answer,
which is S-7.8's construction generalised: there, the gate had no parameter a
claim could occupy; here, the claim is welcome and powerless.

### Three verdicts, because there are three next moves

`HOLDS`, `FAILS`, `UNKNOWN`. A predicate that could not be evaluated is not a
predicate that failed: *migrations are unapplied* and *nothing could be asked
because the database is not answering* send a reader to two different places, and
reporting the second as the first is S-7.2's flattened-ignorance mistake in a new
costume.

**`UNKNOWN` does not complete a stage.** A stage nobody could measure is not a
stage that passed, and treating ignorance as progress is how a run arrives at the
final gate having skipped everything. `first_incomplete` therefore reports the
first stage that does not *hold*, rather than the first that fails — a stage whose
prerequisites are missing is exactly where the run has stopped.

### The seeding predicate excludes the framework's own tables

ADR 009 writes the seed predicate as *row counts exceed a stated threshold in at
least two tables*. Implemented literally, **it is satisfied by a freshly migrated,
entirely empty repository**: `migrate` populates `django_content_type` and
`auth_permission` by itself, which is two tables above any small threshold. The
Explorer would proceed to measure nothing.

So the predicate counts only the application's own tables, and reports the
framework's row count separately — they are real rows a reader comparing two runs
should see, and what they are not is data. The threshold is ten, which is S-7.8's
small scale point: a database is seeded when it holds enough to be *measured*.
Two tables rather than one, because the costs this system looks for live in
relationships, and one populated table with nothing pointing at it cannot exhibit
a per-parent cost at all.

### The predicates delegate rather than re-deriving

`configure` runs the framework's own `check` command, because Django knows what a
misconfigured Django looks like and this module does not; reimplementing that
judgement would be a second opinion to keep in step with the first. `auth` reads
S-7.4's resolution and `work` reads S-7.8's verification rather than recomputing
either — ADR 009 says the last row is S-7.8 unchanged, and the same logic applies
one row up.

The three questions only the framework can answer — does it import, does a
trivial query succeed, are there unapplied migrations — are asked in **one**
subprocess, so a full nine-stage report costs one `django.setup()` rather than
three.

## Consequences

**Makes easy.** S-7.10 becomes writable: *which stage never completed* is a
property of a `Progress`, and the report already carries the stage's own
definition of done beside what was measured. S-13.1 gets the natural key ADR 009
predicted — a playbook entry scoped to a stage can be measured against that
stage's predicate directly.

**Makes hard.** Nine predicates per framework is real adapter surface and E14
pays it again for every framework. The cost was accepted in ADR 009 and nothing
here changes the arithmetic; what this story adds is that the surface is a single
typed table, so a second adapter is a second key rather than a second design.

**Rules out.** Agent-reported progress. A stage cannot be completed by assertion,
and a stage nobody could measure cannot be completed at all.

**Sabotage-verified on twenty-six properties across two passes, all caught —
after three survived.** All five attacks on the safety properties were caught on
the first pass: a claim advancing whatever the predicate said, `UNKNOWN` counting
as complete, every verdict counting as complete, a run complete when *any* stage
holds, and the stopping point skipping stages that could not be evaluated.

**All three survivors were the same gap — no test reached the negative case.**
`connect`, `endpoint` and `auth` were each only ever exercised in the state where
they hold: SQLite opens anything it can create, the fixture project always had a
route, and the auth resolution was only ever `None`. Each now has a subject that
fails it — a database path in a directory that does not exist, an empty
`urlpatterns`, and a resolution whose route needs a credential nobody made — plus
the control that an *open* route satisfies the stage rather than failing it.

The generalisation is worth keeping: **a predicate tested only where it holds is
a predicate tested as a constant.** The fixture that makes the positive case easy
is usually the reason the negative case is missing.

## A note on the test that failed first

The first version of the test asserting that framework rows are *reported* was
written as `assert "0 row(s) exist…" not in detail` — and `"0 row(s)"` is a
substring of `"30 row(s)"`, so the assertion passed on exactly the output it was
written to reject. It now matches the number with a regex and asserts it is above
zero. Recorded because it is the same family as the weak assertions the sabotage
passes keep finding, caught here by the test failing rather than by the sabotage:
**a negative assertion over formatted text is a substring check, and numbers make
substrings of each other.**
