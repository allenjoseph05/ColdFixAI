# 080 — Three bounds, and a failure somebody can act on

**Status:** accepted
**Story:** S-7.10 — caps and honest failure
**Date:** 2026-08-14

## Context

Five acceptance criteria — a 60-step cap; escalation after 15 steps with no new
information; a per-stage attempt budget on top of the global cap; a failure that
reports **which stage never completed** and what was attempted there; and never
reporting success when no workload does real work.

Two of those already existed. S-5.4 compiles `GROUND` at sixty steps with a
disposition of `ABORT`, refuses the next step *before* the work rather than
after, and has a stall check whose whole argument is that what counts as new
information is decided by the harness. Reimplementing any of it would be a second
set of caps to keep in step with the first.

What S-5.4 could not supply is the two things this story is actually about.

## Decision

### S-7.11 gives "no new information" something to mean

S-5.4's stall check compares a digest of what a step *concluded*. Left to
grounding, that digest would have to be the agent's own account of its progress —
which is precisely the self-judged criterion `08-audit.md` F6 exists to remove.

With nine harness-computed predicates, the digest is the **stage report**:
fifteen steps that leave all nine verdicts unchanged have taught the run nothing,
whoever believes otherwise. This is the payoff ADR 009 predicted, arriving one
story later than the predicates themselves.

**The digest is the verdicts and not the details.** A predicate's detail string
carries row counts and error text that drift between otherwise identical steps,
and a digest that moved on those would never detect a stall at all — S-5.2's
argument about durations, one layer up.

### Grounding stalls at fifteen, not at three

S-5.4's default is three, and it is right for an investigation: two identical
results is a confirmation, which is a thing an investigation legitimately does. It
is wrong here. A grounding stage is approached by trying one thing after another
— install the driver, install the other driver, set the environment variable —
and each failed attempt leaves the report unchanged without meaning the run has
stopped learning.

A budget constructed with the wrong value is **refused rather than corrected**.
Silently substituting the right one would hide that the caller asked for
something else, and a run escalating after three unchanged reports would abandon a
repository mid-install.

### The per-stage budget is the tighter instrument, and it dominates

Eight attempts per stage, chosen against S-0.3 rather than guessed: the worst
single stage across three repositories took six distinct attempts, so eight
leaves room above the worst case actually observed and still fails a hopeless
stage in under a seventh of the global cap.

**With the defaults, a run hammering one stage never reaches the stall check** —
the per-stage budget stops it at eight, five steps before fifteen. That is the
backlog note's point ("the per-stage budget is the tighter instrument") and it is
asserted by a test rather than left as an inference. What the global stall
catches is the other shape: an agent moving *between* stages, spending steps, and
changing nothing anywhere.

### A failure names the stage, its predicate, and what was tried there

The backlog note is the specification: *reports what was attempted* is a
transcript, and *stage four never completed, here is its predicate and the last
error* is something a user can act on. So `Failure.report()` leads with the
incomplete stage, states what done would have meant in ADR 009's own words, gives
the last measurement, and lists **only the attempts made at that stage** — the
other fifty are noise to somebody trying to fix this one.

`give_up` returns a `Failure` rather than raising. An agent choosing to stop is
not an error, and `08-audit.md`'s null-result rule makes an honest *this will not
ground* a legitimate output rather than a failure to produce one.

### There is exactly one way to succeed

AC 5 asks that the run never report success when no workload does real work, and
the guarantee is that `finish` is the only method returning an `EmittedWorkload`.
It calls S-7.9's `emit`, which calls S-7.8's `accept`, which reads a verdict
computed from measurements the harness took. Every other exit produces a
`Failure`.

A test asserts by inspection that no other method returns one, which is the same
construction S-7.8 used on `accept`'s signature: the property is checkable rather
than described, and it fails the moment somebody adds a second exit.

## Consequences

**Makes easy.** S-17.2 gets a publishable failure report. S-13.1 gets attempts
already keyed by stage, which is the shape ADR 009 said its playbook entries
would need.

**Makes hard.** Every attempt evaluates all nine predicates, which is three
subprocesses against a real subject. That is the price of an objective stall
check, and it is bounded by the sixty-step cap; the predicates were built to
share one probe between four of the nine for exactly this reason.

**Rules out.** Agent-reported progress, a second set of caps, and a success path
that does not pass S-7.8's gate.

**Sabotage-verified on sixteen properties across two passes, all caught — after
one survived.** The survivor was **the recurring shape, a fourth time**: the
failure report names `first_incomplete`, and every test in the file attempted the
stage that was *also* the incomplete one — so *last attempted* and *first
incomplete* coincided, and a report built on either read identically. A run that
touches `migrate` (which fails) and then `seed` (later in the order, also
incomplete) separates them, and the report must name `migrate`.

The generalisation is now stated four times in four stories and is worth
promoting to a habit: **before asserting that a value comes from rule A rather
than rule B, check the fixture makes A and B disagree.** Every instance so far
has been a fixture where they happened to coincide.
