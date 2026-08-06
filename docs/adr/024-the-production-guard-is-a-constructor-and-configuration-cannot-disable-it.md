# 024 — The production guard is a constructor, and configuration cannot disable it

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.5 requires the system to refuse to start unless the database URL matches a
configured test pattern, with the check running before any other
initialization, an error naming what was expected and what was found, and **no
override flag**.

ADR 007 supplies the stakes: *every safety property in this system assumes state
can be reset ten times a run; against production that assumption is a data-loss
incident.* S-2.6 — the next story — implements those resets, and one of its
three strategies truncates and reseeds. This guard is what stands between that
code and somebody's customers.

Two things needed deciding. "The check runs before any other initialization" is
an ordering requirement, and orderings are exactly what a growing codebase gets
wrong. And "no override flag" is easy to satisfy literally and easy to violate
in spirit.

## Decision

**The check is the constructor.** `VerifiedDatabase(url)` either returns a
verified handle or raises. There is no unverified handle to hold, no `verify()`
that a caller might forget, and no ordering to get wrong: downstream code takes
a `VerifiedDatabase` rather than a string, so a connection to an unverified
database cannot be *described*, let alone opened. That is how the ordering
requirement is made structural rather than procedural.

This mirrors ADR 022's construction. There the enforcement was an absent method;
here it is an absent unverified state.

**Default-deny on three axes: scheme, host, and database name.** A URL is
refused unless all three are explicitly permitted. The alternative — a denylist
of things that look like production — fails the first time somebody names a
database something the list did not anticipate, and fails silently, in the
direction of destroying data.

**The database name is the load-bearing check, and the host list is the weak
one.** `localhost` is one SSH tunnel from anything, and a production compose
file is as free to call its service `db` as a test one is. What actually
separates the two is that people name production databases after the product and
test databases after testing. The host check is kept as a second layer, not
relied on as the first.

**No override exists, including the ones spelled as configuration.** There is no
`force`, no `allow_production`, no environment variable. The policy is
configurable because the criterion requires a *configured* pattern — but a
policy that admits everything is refused at construction. A `*` in the name
patterns is an override flag with a different name, and so is an empty pattern
list. Both raise.

**The refusal redacts the password.** This exception is destined for a log or a
traceback, and a guard that refused the production database while printing its
credential would be its own kind of incident. `VerifiedDatabase` also defines
its own `__repr__`: a frozen dataclass renders every field by default, and this
object is designed to be passed around.

**The class is `VerifiedDatabase`, not `TestDatabase`.** The obvious name
collides with pytest's collection convention — pytest tried to collect the class
as a test suite and warned. The rename also reads more honestly: it says the
handle passed a check, rather than asserting what the database is.

## Consequences

**Makes easy.** Answering "could this have run against production" by reading
one constructor. Every future component that needs a database takes a
`VerifiedDatabase`, and none of them re-implements or re-checks anything.

**Makes hard.** Pointing the system at a legitimately-named database that does
not match a conventional test pattern. The remedy is to add the pattern to the
policy, which is deliberate friction — the alternative is a flag, and a flag
gets set once in frustration and never unset.

**Rules out.** A CLI `--force`. A `COLDFIX_I_KNOW_WHAT_IM_DOING`. Anything that
turns a data-loss guard into a prompt.

**Left open, and stated plainly: there is no runnable entry point yet.** The
criterion says "the system refuses to start", and nothing in this repository
starts — E6 owns the orchestrator. What exists now is the stronger half of the
guarantee: no database handle can exist without the check having passed. When an
entry point is built it will construct a `VerifiedDatabase` and get the refusal
for free; it cannot accidentally skip it, because it has nothing else to
construct.

Also open: an SSH tunnel from `localhost:5432` to a production database is
undetectable here. The name check is what would catch it, and only if the
database is honestly named. No URL-pattern check can do better, which is a limit
of the mechanism rather than of this implementation.

## Provenance

`docs/10-BACKLOG.md` S-2.5; `00-BRIEF.md` §3, which names the mechanism as a
database-URL pattern check; ADR 007, which supplies the reason and quotes
`CLAUDE.md`'s rule that a dangerous thing prevented by documentation needs code
instead — *"This one has code."*

**Sabotage-verified on five properties, and two of the sabotages taught a
lesson about sabotage.** Dropping the name check fails 6 tests; using a denylist
for hosts fails 2; permitting a vacuous policy fails 1; removing the custom
`__repr__` fails the credential-leak test; and un-redacting the error fails its
own.

The last two **initially reported no failures, and both were wrong** — the
sabotage had not applied. `@dataclass` does not overwrite a `__repr__` defined
in the class body, so deleting `repr=False` changed nothing; and a `sed` pattern
containing `\n` does not match a literal backslash-n in source. A sabotage that
reports no failures must be checked for having actually been applied, or it
reads as evidence of a property it never tested. Both were redone with an
assertion that the text changed, and both then failed correctly.
