# 075 — The ORM declares and the database decides

**Status:** accepted
**Story:** S-7.6 — fixture synthesis
**Date:** 2026-08-13

## Context

Four acceptance criteria — read the schema and walk foreign key chains to
construct valid rows; handle required fields, enums and unique constraints;
**handle multi-level FK chains discovered on `IntegrityError`**; fall back
gracefully and report when synthesis fails.

The third one names an instrument, and it names it because a schema lies about
itself in three ordinary ways:

- **`blank=True` is a form concept.** A `blank=True, null=False` column is
  required by every database and optional in every admin form. It is the
  commonest way a plan built from the models alone fails on its very first row.
- **A `UniqueConstraint` is invisible on the fields it covers.** Nothing on
  `Book.code` says it is unique when the uniqueness lives in `Meta.constraints`.
- **Migrations and models drift.** An unapplied migration leaves the database
  stricter than the models describing it, and the ORM is the one that is wrong.

So this is ADR 070/072/073/074's distinction once more, with the roles unusually
sharp: the ORM's schema is the **declaration**, and `IntegrityError` is the
**enforcement**.

## Decision

### The loop is plan, attempt, read the refusal, revise

A first plan supplies every column the models say is required. The database is
what decides, and each refusal names one constraint the plan did not know about:
a missing column is added, a repeated value is made to vary, and the plan is
submitted again. `MAX_REVISIONS` bounds *unknown* constraints rather than effort.

**The planner is a pure function over a schema mapping**, and only the executor
runs in the subject's interpreter. That is deliberate: it puts the interesting
logic — ordering, counts, value choice, revision — where it is typed and unit
tested, and leaves a thin program that creates rows and reports what was refused.

### An error message is not a diagnosis

S-7.2's rule, second occurrence. Integrity error text differs by driver, server
version and locale. `psycopg` carries the server's own structured diagnostics —
`column_name`, `constraint_name`, `table_name` — so those are read first, and
patterns over the message are the fallback for SQLite. **`Learned` records which
of the two settled it**, because a revision built on a regex over someone's
locale-translated error is worth less than one built on the server's own field,
and a reader deserves to know which they have.

A refusal naming nothing — a check constraint, a trigger — is **not revised**.
`Violation.OTHER` is reported, because the same plan submitted again is not made
satisfiable by being submitted again.

### The whole plan is one transaction

**Found by a real run, not by review.** With one transaction per step, the first
attempt created its publishers and authors, failed on a unique book code, and the
*revision* then failed on the author e-mail it had itself inserted a second
earlier. A retry loop that writes either rolls back what it wrote or poisons its
own next attempt — and rows left behind by a failed attempt would inflate S-7.5's
`grew` counts and S-7.8's scaling regardless of whether synthesis eventually
succeeded.

The exception is caught *outside* the atomic block, so the rollback has already
happened when anything is reported; querying inside a broken atomic block raises
`TransactionManagementError` and would bury the integrity error underneath it.

### Refusals before writes, where a refusal is possible

A column whose type this cannot fill is reported **before anything is written**,
with the model, the field and the type. Writing `None` into a `NOT NULL` column
fails at the database with a worse message, and writing a zero into a column that
means something else produces rows that are valid and meaningless. A required
foreign key cycle is reported the same way: no order of inserts satisfies it.

A field with `choices` takes one of them. Django does not enforce choices at the
database level, so a row holding `coldfix-0` in a status column inserts cleanly
and then breaks the application that reads it — and a workload built on such rows
measures error handling.

### Synthesized data is uniform by construction, and says so

`07-use-cases.md` §5 is blunt: *if every generated customer has three orders, an
N+1 that only hurts customers with three thousand orders stays invisible*, and
S-3.3 proved the uniform fixture is the provably blindest shape for any
per-parent cost. The emitted `FixtureRecipe` says `UNIFORM` because that is what
was built, and `Synthesis.blindness` states the consequence in words so that it
travels with the fixture — `CLAUDE.md`'s rule that exclusions carry their
preconditions.

**This is the mirror of S-7.5 and the difference is worth naming.** There, the
distribution is *measured* and a non-uniform spread is refused rather than
labelled. Here it is *chosen*, and until S-7.7 the only shape that can be chosen
is the blindest one. Describing found data and describing made data are different
obligations.

## Consequences

**Makes easy.** S-7.5's `NeedsSynthesis` now has a floor to return to, and the
Explorer can ground a repository with no factories at all. AC 3's loop doubles as
a diagnostic: a subject needing four revisions is a subject whose models and
migrations have drifted, which is worth reporting even on success.

**Makes hard.** Two databases means two error dialects, and SQLite offers no
structured diagnostics at all — so the pattern table is a maintenance surface
that will need a third entry the first time a subject uses MySQL. `Learned`
exists so that the weakness is visible rather than assumed away.

**Rules out.** Reading a schema and calling the result a specification. Retrying
a write loop without rolling back what the last attempt wrote.

**Sabotage-verified on twenty-nine properties across two passes, all caught —
after one survived.** The survivor was **an unreachable branch**, the third of the
three known causes: nothing in the suite made the subject's program fail to
answer, so replacing that refusal with an empty payload changed no outcome. It
now has a test that breaks the settings module — because *this project has no
models* and *this project would not load* are two answers, and flattened into one
the second becomes a plan refusing every target as unknown, which reads as a bad
argument rather than a broken subject.
