# 114 — A question nobody asked is not a clean answer

**Status:** accepted
**Story:** S-11.3 — cheat detection
**Date:** 2026-08-18

## Context

S-11.2 asked whether the patched program still behaves the same. This asks the
other half: whether it is actually faster, or only appears to be.

`02-architecture.md` §210 and `03-agents.md` §412 name five ways an improvement
can be unreal. Four of them are only visible in a number some adapters produce
and others do not — a row count, a byte count, a whole-process total, a second
fixture shape.

## Decisions

### 1. The five classes are S-10.1's enum, imported

S-10.1 built `Cheat` as an enum rather than §5.4's `list[str]` **for this story**,
in as many words: *S-11.3 has to ask could a cheat of class X pass this test and
needs the same vocabulary to ask it in.* So this module imports it. A private copy
would be two vocabularies at the one join that has to agree — a falsification test
declaring what it catches, and an audit declaring what it found.

### 2. Nothing here measures

`CLAUDE.md` puts the measuring in the harness, and S-9.6 records why an auditor
producing its own numbers would be the one place that rule could not be enforced.
This module is handed a `Measure` and decides what to ask for; the harness decides
how to run it and what it costs.

`_read` checks that a reading is of the revision and shape that were **asked
for**. A harness that ignored its arguments would hand back the same numbers for
both revisions, every class would come back absent, and the patch would be cleared
by a measurement that never distinguished it from the original.

### 3. `UNTESTED` is a third answer, and it is the whole shape of the module

The obvious implementation skips the classes whose metric it lacks and reports the
rest as passing. That reads as *five checks, nothing found* and means *one check,
nothing found*.

So every class returns `DETECTED`, `NOT_DETECTED` or `UNTESTED`; `CheatAudit`
refuses to be built unless all five appear; and `clean` is false while any of them
is untested. S-3.1's *no* against *not known*, and S-11.2's `survived` in the same
shape one story later.

### 4. Cold is the first pass through a fresh process

`primitives/measurement.py` already names the hazard: *work the first run warmed
is free for the second, so the second looks cheaper than it is.* A `Reading` is one
fresh process driven more than once, keeping the cold pass apart from those that
followed — one container run per revision, answering both halves of the story.
The improvement warm is the repeated passes compared; the improvement **cold** is
the first passes compared, and AC 2 is that the second exists wherever the first
does.

### 5. The original is the control, because every framework warms

Django fills a connection pool, compiles templates and populates an app registry
on the first request through **any** codebase. An audit reading *the later passes
were faster* as cached state would accuse every patch it ever measured. What is
reported is the patch's warm-up **in excess of** the original's.

### 6. What counts as a real move is S-9.6's rule, not a new threshold

A count is exact and reproduces to the integer, so any fall is real; a duration is
one sample against a floor S-0.4 measured at about 12%, so only a fall past it is
evidence. Re-deriving thresholds would be a second answer to a question this
project has answered once.

`Metrics` refuses to exist if any metric it names has no declared kind. Defaulting
would compare a metric under whichever rule happened to be the default, and the two
rules disagree about every small move.

### 7. S-11.2 answers the stubbed-response class better, and goes first

That story drives real payloads through both revisions and returns a reproducing
input. A response-size comparison is a proxy, and a proxy that disagreed with the
real comparison would be the weaker number overruling the stronger one. The size
is used only where no equivalence attack settled it.

### 8. One fixture shape cannot answer the shape-specific class

A special case for the seeded shape looks exactly like a general fix **when
measured on that shape** — S-9.3's argument, arriving two epics later at the
artifact it warned about. With no alternative shape supplied that class is
`UNTESTED`, never clean.

## Consequences

**A test found a real hole by expecting the wrong thing.** It expected `detect` to
raise on an undeclared metric kind; `detect` succeeded. With the check only inside
`kind_of`, a configuration naming a cost metric with no declared kind produced a
*complete* audit — every class `UNTESTED` for want of **other** metrics, so nothing
ever asked what rule the cost metric moved under — and the error waited to surface
from a property, on an artifact a verdict could already read. The check moved to
`Metrics.__post_init__`.

**The substring-over-source trap, for the fourth time.** `"class Cheat" not in
source` matched `class CheatError`. Recorded at S-7.11, again at S-9.3, again at
S-10.1, and again here. The test now asserts that the name bound in the module
**is** the object from `falsification`, which no prefix can fake.

**`Finding.answered` was dead and was deleted rather than tested.** A sabotage
made it return `True` unconditionally and nothing failed, because nothing called
it. `CLAUDE.md`'s rule is that a path with no caller is deleted, not covered.

**`clean` has three clauses and every existing failure was overdetermined.**
Dropping any one of them left the whole suite passing: each test that asserted
`not clean` had two or three clauses failing at once. Separating them needed a
case per clause — in particular a **warm-only gain too small to trip the caching
check**, which is complete, detects nothing, and still is not an improvement
anybody gets.

**Sabotage: 41 properties, all caught, zero skipped, after five survived.**
