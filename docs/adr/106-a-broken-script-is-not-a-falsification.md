# 106 — A broken script is not a falsification

**Status:** accepted
**Story:** S-10.2 — must-fail gate
**Date:** 2026-08-17

## Context

`03-agents.md` §5.3: *a test that passes before you change anything is testing
nothing. This gate costs one script and kills entire wasted branches.*

S-10.1 produces a falsification test and deliberately cannot say whether it
failed — that would be the agent reporting a measurement. This story runs it.

## Decisions

### 1. "Unpatched" is a property of the type

The gate takes a `DiagnosticSession` and nothing else. S-2.3 built that class
with `apply_patch` and `diff` deliberately absent — *every operation that could
carry a change out of this session is absent rather than guarded* — so a patch
has **no route into** the worktree the gate runs against.

A gate accepting any session would be checking a claim about which revision the
caller happened to check out, which is the class of criterion that reads as met
and is satisfied by convention. This is S-2.3's separation earning itself two
epics later.

### 2. Three outcomes, and the third is the whole design

The obvious implementation reads *non-zero exit* as *the test failed*. Under that
rule a script with a syntax error, a bad import or a missing fixture **authorizes
patch generation** — the gate inverted.

So the script runs under a wrapper that separates an `AssertionError` from
everything else, and only that authorizes a patch:

| exit | meaning | next |
|---|---|---|
| 0 | nothing raised | vacuous — stop, no patch |
| 1 | an assertion failed | **falsified** — proceed |
| 3 | anything else raised | the script is broken — repair it |

Plus a timeout, which is none of the three: a killed run proves nothing in either
direction.

This is S-3.1's *no* against *not known* at the top of the repair phase, and the
same distinction S-9.6 drew for a metric that vanished. Each refusal carries a
different remedy because a passing test is a branch to abandon and an errored one
is a script to repair — sending the second back as the first would have the
Surgeon rewriting a correct test because of a typo.

### 3. The script is never written into the subject's tree

It travels on the command line. S-2.4 refuses a patch that touches a test, so a
falsification script materialised as a file in the repository would be a test the
patch must not touch and which every later diff would show. `03-agents.md` §5.2
lists a `write_test(script)` tool; this is that tool's effect without the file.

`repr` embeds the script rather than interpolation, so quoting is the
interpreter's problem, and `compile` names it `falsification_test` so a traceback
points somewhere.

### 4. `Falsified` refuses to represent a passing run

S-2.7's construction — a type whose constructor will not describe a failure as a
success — applied to the artifact S-10.4 will require. It carries the run's own
output, because *the test failed* with nothing under it is an assertion by
whoever wanted it to have failed.

`Falsified | NotFalsified` are exclusive by construction, so a caller cannot
proceed without having branched. That is S-7.1's `Fingerprint | Unsupported`.

## Consequences

**A real defect, found by a test written before the code was trusted.** `compile`
was outside the guarded block, so a script with a **syntax error** raised
`SyntaxError` before the `try` and the interpreter exited **1** — which is
`FAILED_EXIT`. A malformed script read as a falsification and would have
authorized a patch.

That is the exact failure this module's three-outcome design exists to prevent,
reintroduced by two lines of indentation, and it survived my own review: the test
docstring predicted it (*a syntax error fails at `compile`, not at `exec`*) while
the code did the opposite. The protocol is now verified against a **real
interpreter** for all four cases rather than assumed.

**Sabotage: 20 properties, all caught, zero skipped, after two survived and three
were skipped.** One survivor was my own broken sabotage — an `if False else` that
changed nothing — which is worth recording because a sabotage that does not
sabotage reads exactly like a property that is well tested. The real survivor:
**dropping `wrap` entirely changed no outcome**, because the fake session returns
a canned exit code whatever it is handed and the test only checked the script was
*in* the command, which it is either way. Without the wrapper there is no
protocol at all — a bare script exits 1 for an assertion and 1 for a syntax
error.

The three skips were patterns that stopped matching after `ruff format` reflowed
the source. Third occurrence of that class; the skip count S-9.7 added to the
runner is what makes it visible rather than silent.
