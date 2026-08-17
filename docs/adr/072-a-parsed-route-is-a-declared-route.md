# 072 — A parsed route is a declared route, and only the framework has the table

**Status:** accepted
**Story:** S-7.3 — entry point enumeration
**Date:** 2026-08-11

## Context

Three acceptance criteria — enumerate HTTP routes, CLI entry points, management
commands, background job handlers and integration tests; rank the candidates by
likely usefulness; **handle frameworks where routes are dynamically registered.**

The third is the one that decides the design. A Django URLconf is not
configuration, it is a module: a DRF router registers six routes per viewset when
it imports, `include()` splices another application's table in under a prefix,
and a comprehension over a list of models produces a route per model. None of
those exist in the file as text. A parser that reads `path(...)` calls does not
miss them by being weak — it misses them by being a parser.

This is S-7.1's finding one level down. There, a manifest's `django>=5.0` is a
*constraint* and the installed version is a different fact requiring a different
instrument. Here, a `path()` call is a *declaration* and the route table is a
different fact requiring a different instrument.

## Decision

### Two enumerators with different standing, kept apart

| Discovery | Establishes | Cannot establish |
|---|---|---|
| `PARSED` | this call appears in this file | that it registers, or what prefix it ends up under |
| `RESOLVED` | the framework reports this route as live | — it is the route table |

Resolution runs a program in the **subject's** interpreter that calls
`django.setup()`, walks `get_resolver().url_patterns` and prints JSON. That is
why `django` is a *dev* dependency and not a project one: nothing under `src/`
imports it — the program is source text — and the alternative to installing it
was testing AC 3 against a fake resolver, which would have asserted only what the
fake was written to believe.

### The route table is claimed complete only when the framework answered

`routes_are_complete` is false whenever resolution did not run. A parse cannot
prove completeness of something built by running code, and ADR 009's *endpoint*
predicate — *at least one candidate route was enumerated* — would otherwise be
satisfied by a repository whose routes are all registered by a router.

**It is also false when the resolver reported a problem it could not follow**,
and that half was added because a test found it. `include("shop.api")` imports
eagerly, so one application's broken URLconf takes the whole table with it: the
resolver *answers*, with zero routes and one problem. Counted as
available-therefore-complete, that reports a repository as having no endpoints
when what happened is that nobody could read them.

### What a parse cannot expand is recorded, never dropped

A `path()` whose pattern is not a literal, and a `router.register(...)`, are both
places the file registers routes that reading it cannot enumerate. They are
reported as `Unexpanded` with the construct and the reason. This is the honest
form of AC 3 before an environment exists, which is when the Explorer first needs
an answer.

An `include(...)` mount is recorded the same way and **is not emitted as a
candidate**. Requesting the prefix alone returns 404, and scored as a
parameterless route it would sit at the very top of the list the Explorer works
down.

### Ranking is anchored on S-7.8, and says that it is a prior

"Likely usefulness" needs an anchor or it is a pile of preferences. There is
exactly one downstream gate — S-7.8 rejects a workload unless query count,
response bytes and wall time all move between N=10 and N=100 — so a candidate is
useful to the degree it looks able to pass that, and every term is a reason to
expect it will or will not:

| Term | Reason |
|---|---|
| kind, 1–4 | how directly the thing can be driven at two scales and observed |
| no path parameter, +4 | it addresses a *set*, and a set is what grows |
| each parameter, −2 | it addresses one object, which returns one object at every N |
| each segment of depth, −1 | a deeper path names a narrower thing |
| framework or plumbing, −10 | code this system refuses to patch, or an endpoint designed to do no work |

Every score carries the reasons that produced it, and the report states in words
that this is a prior about what to try rather than a measurement of anything.
Infrastructure routes are **ranked last, never dropped** — AC 1 is enumeration
and AC 2 is order, and dropping the admin would hide it.

## Consequences

**Three unfamiliar repositories were enumerated, and two ranking rules came out
of it rather than out of review.** netbox ranked **thirty-nine** routes level at
the top with no depth term, ordered alphabetically among them — a ranking that
does not rank; with it, eighteen. And matching infrastructure on the first path
segment only makes the rule about where an application chose to mount its auth
rather than about what the route does: a login page costs the same against ten
rows and ten million wherever it sits.

netbox also produced **430 unexpanded registration sites** against 86 parsed
routes. AC 3 is not a hypothetical about DRF; it is most of that repository.

**One incident worth recording.** The enumerator was run against S-0.6's holdout
during development, and `tests/test_holdout_discipline.py` caught it — the guard
working exactly as ADR 011 intended. The depth rule above was re-derived from
netbox alone and stands on it; the any-segment rule stands on the a priori
reason, not on what the holdout showed. Nothing was added to `ALLOWED`, and the
committed tests name only the development targets. Recorded here rather than
quietly fixed, on the same principle that keeps S-0.3's contamination visible in
that test's comments.

**Makes easy.** ADR 009's *endpoint* predicate becomes computable, and honestly:
a repository whose table could not be read reports that, rather than reporting no
endpoints. S-7.4 gets routes to attempt auth against; S-7.9 gets a route name to
address one by.

**Makes hard.** The parsed and resolved tables do not share a spelling — a
fragment is `books/` and the route is `api/books/` — so comparing them at all
needs a reduction, and `dynamically_registered` compares the last literal segment
and deliberately under-reports. Two applications' `badges/` in one repository are
two different routes, which is the same fact from the other side.

**Rules out.** Reading a route table out of files and calling it the route table.

**Sabotage-verified on twenty-five properties, all caught — after four survived
the first pass.** Three were **weak tests rather than weak code**, and all three
passed for a reason other than the one they claimed: the parameter-penalty test
compared routes of different depth, so the depth term alone separated them; the
management-command test compared `rebuild_reports` with `migrate`, so the
collection-name bonus alone separated them; and there was no test reaching the
branch where the subject's interpreter cannot be *started* at all, since a
settings module that will not import is a command that runs and exits non-zero.
The fourth was a harness fault — a pattern that never matched, which reads as
evidence for a property it never tested. **Ninth time a passing sabotage has
meant a weak test.** Baseline re-run green after the pass.
