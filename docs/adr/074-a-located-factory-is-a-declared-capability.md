# 074 — A located factory is a declared capability; a recipe is what a run wrote

**Status:** accepted
**Story:** S-7.5 — fixture discovery
**Date:** 2026-08-13

## Context

Three acceptance criteria — locate existing factories or fixtures (factory_boy,
pytest fixtures, management commands); use them in preference to synthesis;
record the fixture recipe in the workload artifact.

The third one decides the design, and it decides it by arithmetic rather than by
taste. S-4.1 already defines the artifact, and `FixtureRecipe` requires an
`entity`, a `per_parent` and a `distribution` — *how many rows of what, spread
how*. **A file establishes none of the three.** `BookFactory` says it makes books.
It does not say that `create_batch(10)` also wrote ten authors through a
`SubFactory`, and `per_parent` is exactly that ratio.

So this is the epic's running distinction a fourth time — ADR 070 (a declared
version is not an installed one), ADR 072 (a parsed route is a declared route),
ADR 073 (a declared scheme is not an enforced one) — except that here it is
forced by an existing schema rather than chosen. Minting a recipe from the parse
would put three numbers nobody measured into the artifact that keys S-5.1's
replay cache and carries S-3.3's blindness qualification.

## Decision

### Discovery locates and ranks; only a run mints a recipe

`discover()` reads files and returns `Mechanism`s, every field of which is
declared. `exercise_factory()` counts every model's rows, runs the thing, counts
again, and returns the difference. `recipe_from()` accepts only an `Exercise`.

### Preference is anchored on S-7.8, and the anchor is whether the size can vary

AC 2 says to prefer what exists over what would be synthesized. Among the things
that exist, the ranking needs an anchor or it is a pile of preferences, and there
is one downstream gate: S-7.8 rejects a workload unless it can be driven at N=10
*and* N=100.

| Term | Reason |
|---|---|
| kind, 1–4 | how directly it can be called and whether it says what it makes |
| takes a count, +4 | the size is an argument, which is what `scale(n)` needs |
| fixed set of rows, −6 | it cannot produce a second scale at all |
| names its model, +1 | the entity is known before it is run |
| contents countable, +1 | a fixture file is a list of objects |

**A `loaddata` fixture file is worth least**, and that is the useful finding
rather than a slight: it holds fixed rows with fixed primary keys, so loading it
twice is a collision, not twice the data. **A repository whose only fixture is a
`.json` file needs S-7.6 despite having a fixture**, and `prefer()` says so with
the file named — `NeedsSynthesis` carries what was located, because *this
repository has a fixture and it cannot be scaled* and *this repository has
nothing* send a reader to two different places (S-7.1's rule for unsupported
frameworks).

A pytest fixture is located because AC 1 names it, and never chosen: it is only
callable from inside a pytest session, so driving one means running the subject's
test suite, and S-2.4 forbids editing a test to change what it seeds.

### The distribution is measured, and refused when it cannot be named

S-3.3 proved the uniform fixture is provably the blindest for any per-parent
cost, which makes `UNIFORM` the one value that must never be assumed. It is
established by a real `GROUP BY` over the child table — because forty children
over ten parents divides evenly and says nothing about whether one parent holds
thirty-one, and that division is the assumption S-3.3 is *about*.

**Parents holding nothing are counted too.** They do not appear in a grouping
over the child table, and leaving them out makes *nine parents with one child and
one with none* indistinguishable from *nine parents with one child* — only the
second is uniform.

A spread that is not uniform **does not become `POWER_LAW` by elimination**.
`Distribution` has three values and none of them means *not uniform*; which
skewed shape a pile of rows has is a fit, and S-7.7 is the story that makes
distribution a parameter of `scale()` rather than a label on found data. So
`recipe_from` refuses, reports the measured spread, and names S-7.7. **This is a
stated limit, not a gap**: the alternative is a fitted label on the field whose
entire purpose is to record which reading was taken.

`per_parent` measured as zero is likewise refused rather than clamped to one —
clamping would record a child per parent that was counted and found not to be
there.

### The entity is the model that grew by what was asked for

Not the largest grower, which names the child of a one-to-many: seeding five
authors with three books each grows the book model by fifteen. `scale(n)` means
n *of the entity*, so the entity is the model whose growth is closest to n.

## Consequences

**`factory-boy` is now a dev dependency**, on the same terms as `django` (ADR
072) and `djangorestframework` (ADR 073): nothing under `src/` imports it, the
exercising program is source text run in the subject's interpreter, and the
alternative was a stub factory seeding exactly what the test file imagined a
factory seeds.

**Writing a real factory found a real defect.** The rule for skipping abstract
base factories was *no `Meta.model`*, which is wrong: a factory that subclasses
another to add a `RelatedFactoryList` declares no `Meta` at all and inherits its
parent's model. That is the ordinary shape of the factory building the most
interesting data — a parent with several children, which is the only shape where
`per_parent` is not 1 — and the rule dropped exactly it. Abstractness is what
`Meta.abstract` says; the model is resolved through bases within the file.

**One test could not have discriminated and was replaced.** `BookFactory` with a
`SubFactory` writes one author per book, so *closest to n* and *largest grower*
name the same model and the entity rule was asserted by a test that both
candidate rules passed. The fixture now includes a factory whose children
outnumber its parents three to one, where the two rules disagree. This is S-7.3's
sharpest lesson — **a comparison whose subjects differ in more than the one thing
under test** — arriving before the sabotage pass rather than during it.

**Makes easy.** ADR 009's seeding stage becomes computable, and S-7.9 gets a
recipe whose every field was measured. S-7.6 gets a floor to sit under rather
than a competitor to be ranked against.

**Makes hard.** The importable module path is a parameter of `exercise_factory`
rather than derived from the file path — a `src/` layout means the checkout root
is not the import root, and guessing wrong produces `ModuleNotFoundError` where
the interesting failure should be a factory raising.

**Rules out.** Reading `per_parent` off a factory's source. Calling a spread
uniform because the totals divide.

**Sabotage-verified on twenty-nine properties across two passes, all caught —
after two survived, and both survivors were the same shape of weak test: a
comparison whose subjects differ in more than the one thing under test.**

The ranking test put a factory against a fixture file, which differ in three
terms — kind, scalability, and whether they name a model — so zeroing the
scalability penalty left the order unchanged and the assertion passing. It is now
two mechanisms differing only in scalability, with the end-to-end ordering kept as
a separate assertion.

And **every exercising test started from an empty database, where a total and a
difference are the same number**, so nothing established that `grew` subtracts at
all. A repository that ships seeded data is ordinary, and there the total would
name the wrong entity and inflate every count in the recipe. There is now a test
that seeds before it measures.

**Eleventh and twelfth times a passing sabotage has meant a weak test**, and both
were S-7.3's recorded shape rather than a new one. One test was additionally
renamed for claiming more than it exercised — it triggers an unimportable module,
not a factory raising.
