# 028 — A refused category and an uncovered one are not the same thing

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.9 lists three things together:

- concurrency and locking findings are `diagnose-only` and can never enter the
  repair path
- causes localized inside third-party dependencies are reported, never patched
- unsupported project types are detected where possible and reported honestly

Reading them as one list invites one implementation. They are not one thing, and
`00-BRIEF.md` §3 already draws the line: it has a **refused on principle** table
of four categories where no verifier this system can build makes a change safe,
and a separate **not covered** list which is a capability boundary. The first two
bullets are the first kind. The third is the second kind.

## Decision

**Concurrency and third-party are enforced structurally, per finding.** The
repair path takes a `RepairableFinding`, and constructing one runs the
classification. A diagnose-only finding has no route to repair — not a rejected
one, an absent one. Fifth use of this construction, after `VerifiedDatabase`,
the session types, `VerifiedReset` and `ScreenedRepository`.

The reasons come straight from ADR 007 and both are carried in the refusal
message, because this is a refusal read by somebody who wanted the fix.
Concurrency: output equivalence is the verification mechanism and cannot detect
an introduced race, so a patch that moves a lock can pass every test on every
run and still be wrong under a scheduling order the suite never produced.
Third-party: the package manager will overwrite the change, the user does not
own the code, and its test suite is not in scope.

**Unsupported project types are reported, not refused, and `report_scope`
returns rather than raising.** A Django application with a React frontend is a
perfectly good subject *for its backend*. Refusing the repository would decline
work this system can do, so the report is per area and says which parts are out
of scope while everything else proceeds. A test asserts this direction
specifically, because collapsing it into a refusal is the obvious tidy-up and
would quietly cost the tool most of its real subjects.

**`classify()` takes a mechanism string and a site path, not a finding object.**
The evidence-chain schema belongs to the Diagnostician (E8) and this check has
to exist before it does. When that schema arrives it supplies these two fields
and nothing here changes. Inventing a `Finding` type in Epic 2 would have E8
inherit a shape it did not choose.

**Erring toward `DIAGNOSE_ONLY` is deliberate**, and it is the opposite of the
bias in S-2.8. Marking a repairable finding diagnose-only costs a fix that could
have been offered. Marking a concurrency finding repairable risks shipping a
race that no check in this system can detect. The asymmetry is real, so
*deadlock-free* is treated as a concurrency mention rather than as a control —
an algorithm chosen for deadlock-freedom is a concurrency change.

**Sites are POSIX regardless of the host.** A site comes from a stack frame
taken inside a Linux container. `Path("/usr/lib/python3.12/json/decoder.py")` is
**not** absolute on Windows, so relying on `Path.is_absolute()` would report
every standard-library site as the user's own code during development here and
as third-party in CI. Found by a test failing on Windows, and worth recording
because the bug is invisible on the platform the system actually runs on.

**Third-party trees are excluded from project-type detection and included in
S-2.8's marker scan.** These look contradictory and are not. React inside
`node_modules` is something the project *uses*; a vendored RTOS inside
`third_party` is something the project *is*. Unpatchable is not the same as
invisible.

## Consequences

**Makes easy.** Reporting a concurrency finding usefully — the diagnosis is
still produced, still measured, still delivered, and only the patch is withheld.
Working on a monorepo, because an out-of-scope frontend does not cost the
backend its analysis.

**Makes hard.** Repairing anything the classifier considers concurrency-related,
including a finding whose mechanism merely mentions a lock in passing. That is
the intended direction, and the evidence string names exactly what matched so a
person can see whether the classification was fair.

**Rules out.** A `--force-repair`. A concurrency fix behind a flag. Treating an
uncovered project type as a refusal.

**Left open.** The repair path does not exist — E10 owns the Surgeon — so what
exists today is the guarantee that when it arrives it has nothing but
`RepairableFinding` to accept. Same qualification as ADR 024's "refuses to
start". And detection of unsupported areas is by manifest and extension, so a
project whose out-of-scope part is undeclared is not detected; the story says
*where possible* for that reason.

## Provenance

`docs/10-BACKLOG.md` S-2.9; `00-BRIEF.md` §3 for the distinction between refused
and uncovered; ADR 007 for both refusal arguments.

Sabotage-verified on five properties, each asserting the edit applied.
A substring search for `lock` instead of `\block\b` fails 5 tests on *clock*,
*unblocking* and *block*. Classifying without blocking the repair path fails 3.
Treating a `package.json` as evidence of a frontend fails the control — the
Django-project-with-Tailwind case, which is most of this system's target
population. Searching inside `node_modules` for project type fails the test that
a dependency is not the project. Raising instead of reporting for unsupported
areas fails the test that keeps monorepos usable.
