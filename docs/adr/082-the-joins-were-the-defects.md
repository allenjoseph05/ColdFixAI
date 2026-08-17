# 082 — Epic 7 composed: every defect was a join

**Status:** accepted
**Story:** Epic 7 composition check
**Date:** 2026-08-14

## Context

Epic 7 finished with twelve stories, 359 passing tests, sabotage passes on every
one of them, and **no way to run one repository through them.** Performing its
own sentence — *turn an unknown repository into a runnable, scalable, resettable
workload* — takes nine modules in the right order, and **no test file in the epic
touched more than three of them.**

The pattern is now four for four. Epic 2's architecture could not run a Django
application at all; Epic 3 found three defects, two about how the registry gets
populated; Epic 5 found four; Epic 6 found three.

## What the composition found

**Six defects, and every one was a join.** Not one was a module being wrong about
its own subject; each was a value that came out of one story in a shape the next
story could not take.

### 1. A ranked route could not be requested as it came out

The resolver reports `books/`. Every downstream story takes a *path* — S-7.4
probes one, S-7.8 drives one — so each caller was left to prepend the slash. The
composed run requested `books/`, got a 404, and…

### 2. …read that 404 as an authentication requirement

`resolve_auth` saw a non-2xx answer, reported the route as unresolved, and the run
stopped with *this route needs a credential nobody made*. The diagnosis was
perfect and the input was wrong. **This is the shape that makes composition checks
worth running**: two correct modules producing a confident, wrong answer between
them.

`Candidate.request_path` now exists, and returns `None` for a *parsed* route —
this module's own distinction, since a parse cannot establish what prefix an
`include()` mounted a fragment under. `Enumeration.drivable` filters the ranked
list to candidates that have an address, because the ranked list mixes both and
leaving every caller to filter is how one of them does not.

### 3. The environment could not reach the artifact

S-7.12 computes an anchor and a resolved dependency set. S-4.1's `Workload` gained
an `EnvironmentAnchor` field to hold one. **Nothing could get from the first to the
second** — `verify_work` is the only code that builds a `Workload`, and it took no
environment. S-7.12's AC 4 was satisfied by a schema and unreachable in practice.

### 4. Seeding always synthesized, ignoring the repository's own factories

S-7.5 exists to *use existing fixtures in preference to synthesis*, and the only
code that seeds at scale called `synthesize` unconditionally. A repository
shipping a perfectly good `BookFactory` was measured against rows this system
invented. The acceptance criterion was honoured inside its own module and
**nowhere else**.

`verify_work` now takes a `Seeder`, defaulting to synthesis; `factory_seeder`
builds one from a located mechanism.

### 5. `target` was required even when a seeder made it meaningless

The seam half-built: a caller supplying a factory still had to name a model for
the synthesis that would not run. Now one of the two is required and the error
says which does what.

### 6. `prefer()` chose the wrong factory, and this is the sharpest one

The subject ships `AuthorFactory` and `BookFactory`. S-7.5 ranks a mechanism by
how well it can seed *two scales* — a property of the mechanism, not of the
workload — so the two tied, and **the alphabetical tie-break chose `Author`**.
The composed run seeded a hundred authors, drove `/books/`, and measured an empty
list: one query, thirteen bytes.

Every S-7.5 test passed throughout, because **none of them had a second factory to
choose between**. The fixture that made the positive case easy is why the defect
was invisible — the same lesson S-7.11 recorded, one level up.

`prefer(discovery, entity=…)` now prefers a mechanism that builds the entity the
workload needs. Nothing infers which entity a route serves; that is the Explorer's
to know, and guessing it from a URL segment would be exactly the inference this
module has declined everywhere else.

## Consequences

**The recurring shape is now nameable.** Three of the six — the route path, the
environment field, the seeder — are the same defect: **a value that one story
produces and another consumes, where nothing in either story's tests holds both
ends.** A module's own tests always supply their own inputs, so they cannot see
this class at all.

**Two of the six were AC satisfied in isolation and unreachable in practice**
(S-7.12's AC 4, S-7.5's AC 2). That is worth remembering when reading a
`DONE` note: *the acceptance criterion is met* and *the criterion is reachable
from the rest of the system* are different claims, and only the composition tests
the second.

**Makes easy.** A caller now works down `enumeration.drivable`, hands
`prefer(discovery, entity=…)` to `factory_seeder`, and passes
`resolved.recorded()` to `verify_work`. Nothing in that sequence requires knowing
which conversions to write.

**Rules out.** Declaring an epic done because its parts are.

**Sabotage-verified on ten properties, all caught — after two survived.** Both
survivors were fixtures that could not discriminate, which is now the fifth
consecutive story to record that shape:

- Removing the *resolved-only* condition from `request_path` changed nothing,
  because every route in the subject was mounted at the root — so a parsed
  fragment happened to be a valid address **by luck**. The subject now mounts a
  route under an `include()` prefix, where the fragment is `orders/` and the
  address is `/api/orders/`.
- Nothing called `verify_work` with neither a target nor a seeder, so the guard
  was unreachable.

The first one is worth keeping in view: the defect this whole composition began
with — a 404 read one story later as a missing credential — could still not be
*sabotage-detected* until the fixture contained a route whose fragment and
address differ. Finding a defect and being able to keep it fixed are two
different pieces of work.
