# 071 — Two probes, not one error message

**Status:** accepted
**Story:** S-7.2 — environment standup
**Date:** 2026-08-11

## Context

Three acceptance criteria — start the database, install dependencies, run
migrations; distinguish *database not started* from *database started but
rejecting connections*; give the agent `logs(service)` and `ps()`. The note under
them is the story: **the two failure states look identical without log access,
and the agent needs those tools or it guesses.**

## Decision

### The two states are told apart by two probes, not by parsing one error

`psycopg` raises `OperationalError` for both, and the text differs by driver
version, locale and server release. Matching on it is a guess dressed as a check.
So the socket is probed on its own, before the protocol:

| Socket | Protocol | State | Repair |
|---|---|---|---|
| refused | — | `NOT_LISTENING` | wait, or check the port |
| accepts | fails | `REFUSING` | read the server's message — waiting will not fix credentials |
| accepts | succeeds | `READY` | nothing |

That is a measurement rather than an interpretation, and it is the difference
between *wait longer* and *fix your password* — the exact fork the note says an
agent cannot see unaided.

### Four states, because there are four repairs

`NO_CONTAINER` is not a flavour of `NOT_LISTENING`: a container that exited and a
server still initialising look identical at the socket and need different fixes,
so Docker is asked before the socket is.

`UNKNOWN` is not a failure. If Docker itself is unreachable, this cannot tell
which of the others holds, and reporting `NO_CONTAINER` would send the agent to
restart a service it cannot see. S-3.1's rule again — ignorance flattened into a
verdict is worse than ignorance reported. A test asserts every state prescribes a
*different* action, so the split cannot decay into decoration.

### The diagnosis carries its probes

The conclusion is a two-step inference, and a reader who disagrees with it needs
the steps. `logs` and `ps` exist for the step after that: the state says which
class of thing is wrong, the log line says which one.

### Migrations are not attempted against a database that is not accepting

The common way standup goes wrong, and it produces exactly the error the note is
about — so the database is diagnosed *between* starting it and migrating, and the
diagnosis travels with the report either way. The commands themselves are
supplied rather than derived: S-7.1 says what the project is, and what stands
*this* project up is a fact about its tooling that E14's adapter owns.

## Consequences

**Two measurements worth keeping.**

The probe timeout is **per resolved address, not per probe**. A host resolving to
both `::1` and `127.0.0.1` with a server on only one pays the timeout once before
reaching the other — measured at four seconds a diagnosis against `localhost` on
Windows, which is most of why the test file dropped from 55 seconds to 19. The
constant now says so, and callers that know a literal address should pass one.

The production guard reached across an epic boundary and was right to. Pointing a
test container's database at `postgres` was refused by S-2.5, because that name
matches none of the test-name patterns — so the container is created with a
`coldfix_*` database instead. A guard that only fired inside its own epic would
not be a guard.

**Makes hard.** `stand_up` takes the three commands as arguments, so it cannot be
called without knowing them. That is correct — deriving them is the adapter's job
— but it means S-7.2 alone does not stand anything up end to end; it is the
sequencing and the diagnosis, not the knowledge.

**Rules out.** Classifying a connection failure by matching its message, and
reporting a state Docker could not confirm.

**Sabotage-verified on twelve properties, all caught — after two survived, both
for want of a test.** There was none forcing the `UNKNOWN` branch, because Docker
is running on this machine and the branch is about what `diagnose` does with
*cannot say*; it is now forced directly. And the stop-at-first-failure test
asserted the *verdict* — continuing past a failure leaves both `stopped_at` and
`succeeded` unchanged, so it passed either way; it now asserts the later steps
were never attempted. Eighth time a passing sabotage has meant a weak test.
