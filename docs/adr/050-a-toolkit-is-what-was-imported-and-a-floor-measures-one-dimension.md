# 050 — A toolkit is what was imported, and a floor measures one dimension

**Status:** accepted
**Story:** Epic 3 composition check
**Date:** 2026-08-08

## Context

Epic 2's composition check found that per-module verification says nothing about
composition: nine stories and 487 passing tests, and the epic could not perform
its own purpose. Epic 3 finished with nineteen stories, thirteen registered
primitives and 880 passing tests, and had never been run as a whole — no test
took a toolkit from the registry and used it on the planted-defect fixture.

Writing that test found three defects. All three are in shipped, individually
tested, sabotage-verified code, and none of them was reachable from a test of one
module.

## Decision

### 1. Importing `coldfix.primitives` registers every primitive

Registration is a side effect of importing the module that declares it. Nothing
imported them all, so `REGISTRY`'s contents depended on what a process happened
to have imported: a caller with `scaling` and `bounds` got a two-instrument
toolkit — and a `Selection` that listed **nothing missing**, because a primitive
nobody imported is not withheld. It does not exist.

Absent and inapplicable are exactly the two answers ADR 030 went to trouble to
separate, and import order silently produced the wrong one. It also broke what
`Selection` is for: a snapshot so the tool list cannot change mid-investigation
(ADR 002, since a growing list invalidates the cached prefix behind it), with
nothing making the list *complete* at the moment the snapshot was taken.

The package now imports all twelve modules. A test reads the package directory,
finds every module containing `REGISTRY.register(`, and asserts each is reachable
as an attribute — asserted from the filesystem rather than from a list in a test,
because a list in a test is forgotten at the same moment as the import.

**This costs about a second, measured.** `-X importtime` puts a cold
`import coldfix.primitives` at ~1.05s: ~690ms of sandbox chain that `ablation`
needs for `DiagnosticSession`, ~290ms of Hypothesis, which is S-3.17's engine.
Paid once per process, and an investigation runs for hours. If CLI startup ever
matters, the fix is a declarative manifest the registry can render without
importing the implementation — a change to the registry's contract, and a story,
not a tidy-up.

### 2. The most decisive verdict wins, not the first one checked

`Primitive.verdict` checked capabilities and returned `UNSUPPORTED` before
consulting the applicability predicate. A subject **known** not to parse
untrusted input, in an environment with no mutation engine, therefore came back
as *unsupported here* — which tells a reader to go and install a fuzzing engine
for a subject that will never need one.

`all_of` already had the right rule and the reason written down: the four states
exist because the reader's next action differs for each, so a definite *no* wins
over an *unknown*. `verdict` now evaluates both and takes the minimum by
`_DECISIVENESS`, the same ordering, so the two paths cannot disagree.

### 3. `fields_required_by` — the row floor measures height, not width

S-3.18 shipped with three computable bounds and was tested against each of them.
Composed against the planted over-fetch, the row floor puts the defect and its
control at exactly **1.0x**: `list_titles_over_fetching` returns the same number
of rows as `list_titles_narrow`, and reports no room in the one workload built to
have some.

Over-fetching is a width defect. `01-primitives.md` §13's own table had named the
case — *serialization: fields consumed downstream vs fields serialized* — and
S-3.18 implemented the other three rows and not that one. The new bound is
computable on the same terms as the row floor: read the width off the response
the workload actually returned. What it deliberately does not do is guess at
fields some further downstream caller might not use, which is intent and which F8
dropped.

## Consequences

The composition also recorded a fixture fact worth having: `linearithmic_sort` is
`sorted()`, so its work happens in C and S-3.19 cannot see it. Comparing it to a
Python quadratic yields two exact counts and no statement about which is faster —
the instrument refusing rather than reporting a C implementation as nearly free.
Anything ranking those two needs S-1.6 and a clock. Noted in the fixture README,
because reaching for that control as an instruction-counting baseline is the
obvious thing to do and it does not work.

Two of the three defects are about the registry, which is `CLAUDE.md`'s one
designed extension point. Both were defects in how the extension point is
*populated* rather than in the mechanism, and neither could be found by testing
the mechanism. That is the same shape as ADR 029 and worth expecting again: the
next composition check should look first at whatever is assembled from parts
supplied by other modules.

Six sabotages, all caught. One had to be rewritten: replacing the field count
with an item count passed, because the over-fetch fixture's response is a list of
plain strings where the two numbers are equal. The test now also runs against a
response of mappings, where they are not.
