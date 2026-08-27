# 149 — A conformance suite that cannot fail is the failure

**Status:** accepted
**Date:** 2026-08-27

## Context

S-14.4 asks for a test suite any adapter must pass, covering the interface
methods plus reset reliability and hook overhead, documented so a third party can
implement an adapter.

Two things about that shape are easy to get wrong, and both produce something
that looks finished.

A suite that lives in `tests/` cannot be run by the third party it is for. And a
suite that has only ever been run against adapters that pass has never been shown
to detect anything — every check could be `return PASSED` and the report would
look identical.

## Decisions

### 1. It ships in `src/`, as a harness

`coldfix/adapters/conformance.py`. Nothing under `src/` calls
`run_conformance`; a person does, which is the same category `eval/ablation.py`
established — operator-driven, imported only by its own tests, and not a gap.

### 2. `SKIPPED` is a third outcome and is never merged into `PASSED`

Most of the interesting checks need something the caller supplies: a session, a
database and a mutating workload, an entry point, a workload that raises counted
events. An adapter run with none of them produces a clean-looking report that
attests almost nothing.

So `conforms` means *nothing failed*, `attested` means *nothing failed and
nothing was skipped*, and `describe()` names the skipped checks in words. The
documentation tells an implementer to read `attested`. This is
`Selection.withheld_notice`'s rule one layer out: an empty result must read as
*these were not run*, never as *these found nothing*.

### 3. The measurement check is self-consistency, because that is what is checkable

**The adapter is the last place in the system where a measurement can be
fabricated.** Only the framework knows how to count its own queries, so nothing
above the adapter can tell an invented number from a measured one, and every
schema accepts it.

The harness cannot check the values. It can check the relationships the caller
already knows: as many samples as repeats requested, a `seconds` that is the
median of those samples, and the scale and row counts handed back unchanged. A
driver that runs once and reports five samples fails, and so does one reporting a
median it never computed — which is the check nothing else in the system would
catch, because `Drive.seconds` is what screening fits a growth curve to.

Beyond that it is the implementer's, and `docs/09-adapters.md` says so: write a
test that seeds more rows and asserts the query count moves, because a counter
wired to a constant passes every structural check there is.

### 4. Reset reliability and hook overhead reuse existing instruments

Reset is S-2.7's `choose_reset` — ten cycles, real row counts — not a new
opinion. Overhead is per event against a **stated denominator**:
`REFERENCE_OPERATION_SECONDS`, ADR 013's measured instrumented database call.

Those two constants moved from S-1.3's test file into `primitives/counters.py`,
because the conformance suite checks a third party's hook against the same bar
and two spellings of one budget drift — with the copy nobody runs being the one
that stays generous. Their new home is beside the `CounterOverhead` enum that
claims `NEGLIGIBLE`, which makes that claim checkable.

### 5. Two checks are weaker than they look, and they say so

Writing the deliberately-broken adapters found this, which is the whole argument
for writing them.

**Protected paths.** `Declarations.patch_policy` concatenates onto the defaults,
so an adapter using it *cannot* narrow them — the first broken adapter declared
one pattern and the check passed, correctly. The remaining route is a
`Declarations` subclass overriding the method; a frozen dataclass is still
subclassable and the Protocol accepts any `Declarations`. The check is kept for
that route and the docstring says the rest is structural.

**Internal frames.** The check synthesizes its stack from the adapter's *own*
first fragment, so a list of plausible nonsense passes: the fragment it names is
the fragment in the stack. It catches an empty declaration and a broken hand-off
and nothing more. A harness that knew what a Flask stack looks like would be a
harness with a framework in it, so the requirement was reworded to what it
actually establishes and the doc tells implementers to write the real test
themselves.

**Both of these were written as checks that overclaimed, and the fakes are what
exposed it.** A conformance suite is exactly the kind of code that is never
exercised in its failing direction unless somebody makes it fail on purpose.

### 6. No check raises

Each catches broadly and turns the exception into a `FAILED` carrying the type
and message. A suite that stopped at the first failure would tell an implementer
about one problem per run, and the whole value of a conformance report is that it
is a list.

## Consequences

**`docs/09-adapters.md` is the third-party guide**, linked from the authority map
in `00-BRIEF.md` and from `CLAUDE.md`. It documents the interface, the two
declarations that are easy to get wrong, the session asymmetry and why it is a
safety property, what the suite cannot check, and — in its last section — that
the grounding sequence is not yet framework-neutral, so an adapter that conforms
will still be refused by the campaign at fingerprinting. Telling an implementer
that up front is worth more than a document that reads as if everything works.

**A fake caught a real Protocol violation before any test ran.** The base fake
adapter declared `run_tests(session: CandidateSession, ...)`, narrowing a
parameter the Protocol widens to `Session`; mypy rejected it on contravariance.
That is S-14.1's *half the story is checked by mypy* paying out on the first
outside implementation.

**Sabotage: 6 properties, 6 caught** — a skip counted as a pass, a fabricated
median accepted, a bypassed patch filter reported as conforming, an overclaimed
capability accepted, an expensive hook inside every budget, and narrowed
protected paths accepted.
