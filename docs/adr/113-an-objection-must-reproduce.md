# 113 — An objection must reproduce, and nothing compared is not nothing found

**Status:** accepted
**Story:** S-11.2 — equivalence attacks
**Date:** 2026-08-18

## Context

S-11.1 gave the Adversary a patch and no account of why it was written. This is
the first attack it can mount: construct the inputs nobody tests with, run both
revisions, and diff what comes back.

Every other instrument in this project produces a number a human reads. This one
produces a verdict a patch ships on, which is the hazard `bench/diffing.py` opens
with — and it is worse here than there, because a difference this module fails to
find is a difference nothing downstream looks for again.

## Decisions

### 1. No model call, and that is the first decision

`03-agents.md` §6.2 gives the Adversary a `craft_input(spec)` tool, which reads
like a generation problem. It is not one. The seven classes are enumerated by the
acceptance criterion and transcribed identically by `02-architecture.md` §209 and
`03-agents.md` §411, so what varies between subjects is *which* of them a workload
can be fed — never what they are. `CLAUDE.md` puts a model call only where a
function will not do, and a catalogue is a function.

The **driver** is the caller's, though. A `Probe` is source text that knows how to
reach one workload, and only whoever grounded the repository knows an endpoint's
signature. Inventing one here would be this module guessing at a program it has
never seen, and a wrong guess produces `NOT_COMPARED` on every input — an attack
that ran and established nothing.

### 2. Nothing loosens the comparison

`diff` makes order-insensitivity opt-in **per comparison** because the decision
belongs to whoever knows whether the query behind the payload had an `ORDER BY` —
and the Adversary is, by construction, the party who does not know. So this module
never opts in, and there is no parameter through which a caller could.

The unordered comparison is still run, but only to **label** a difference as
order-only. The same rows in a different sequence is reported as a fact and the
judgement is left where it belongs: whether a reorder is a behaviour change
depends on whether the endpoint is paginated, which is not a fact this module has.

### 3. Silence is never agreement

The obvious implementation collects payloads, diffs them, and reports no
differences found. That implementation reports a patch as equivalent **precisely
when the probe was broken enough to produce nothing** — the two dead runs agree.

So a run without a parseable payload is `NOT_COMPARED`, never an empty result, and
`Equivalence.survived` is false unless something was actually compared. A stream
whose middle was elided gets its own reason apart from *not JSON*, because a
subject that printed eight megabytes and a probe with a bug are different problems
and only one of them is the probe's fault. `08-audit.md`'s absent-metric rule and
S-3.1's *no* against *not known*, at the last gate before a human sees the change.

### 4. An objection must reproduce, and the original is its own control

AC 3 says *returns a reproducing input*, and the word is load-bearing. On any
difference both revisions are run again — and the **original is checked against
itself first**. A response carrying a timestamp or a fresh uuid differs from its
own second run, and reported as a broken patch it sends the Surgeon to rewrite
code that was right.

A check that re-ran only the *pair* would confirm a difference that the subject
manufactures afresh every time, so the control is the one that matters. Only a
difference surviving the repeat becomes a `ReproducingInput`, and that artifact
carries **the exact program that produces it**: §222 sends `broken` back to the
Surgeon with a reproducing input, and one arriving without the means to re-run it
is a claim the recipient has to take on trust.

Confirmation costs two extra runs and is paid only where something was found.

### 5. An unstable subject is not a clean bill either

`survived` has three conditions, and the third is a judgement. An input where the
subject disagreed with itself says the workload is not deterministic under this
probe — and under that condition the inputs that *matched* matched once, which is
weaker than it reads. A patch audit that resolves doubt in favour of shipping is
the wrong way round.

### 6. The wire is ASCII, and the unicode class is why

The payload is embedded `ensure_ascii=True` and the answer is encoded the same way.
The container's stdout encoding is not ours to choose and `execute` decodes UTF-8
with replacement, so a character mangled in transit is either a difference the
patch did not cause or — mangled identically on both sides — an agreement that was
never tested. Without this the unicode class is the one input class whose result
cannot be trusted.

The catalogue's unicode entries are written as **escapes**, and that is not a style
choice: the NFC/NFD pair are two spellings of one word that render identically in
every editor and in every diff, so written literally a reader could not see that
the two entries differ at all.

### 7. Three constructions carried forward, one guard added

The session types are opposite — `DiagnosticSession` for the revision before,
`CandidateSession` for the one with the change — which is S-10.2's gate and
S-10.6's `verify` used a third time. The probe travels on the command line and is
never written into the tree, because S-2.4 makes a file there a protected path.

New here: the two worktrees are checked for a **common base commit**. A difference
measured across two base revisions is somebody else's change reported as this
patch's, and it looks exactly like a broken patch.

### 8. `page_size` has no default

Probing one under, exactly, and one over a page boundary is the highest-yield
boundary on a list endpoint, and it needs a number nobody in this module knows.
Guessing a common one would let a report claim the page boundary was attacked when
some other number was, so an unsupplied page size means those three inputs are
absent and the report counts what was covered.

## Consequences

**Two tests could not have discriminated the properties they were named for, and
both failed the same way — asserting on the text instead of the protocol.** A
`SyntaxError` prints a perfectly good traceback whether `compile` sits inside the
guarded block or outside it; what changes is that the interpreter picks its own
exit code on the way out. And a missing `output` binding produces
`KeyError: 'output'`, which satisfies an assertion looking for the word "output"
just as well as the harness's own message does. Both now assert the exit code,
which is the thing that actually differs.

That is the S-11.1 fixture failure again in a new costume: **an assertion that
holds under both implementations is not a test**, it is a description.

**The unicode fixtures could not be written as escapes, which is a fact about the
tooling and not about Python.** `"café"` written into these files comes back
as the character, so the argument in decision 6 was defeated by the act of making
it — the pair was invisible again within one commit. They are now built with
`chr(0x00E9)` and `chr(0x0301)`, which nothing normalises and which names the code
point in the source.

**Three survivors, and the third was the interesting one.**

- `Equivalence.survived` dropped its unstable clause and every test still passed.
  Every existing nondeterminism test used a subject that varied on *every* input,
  where `compared` is empty and the **first** condition already answers — so the
  third was unreachable. It takes an input that matched *beside* one that would
  not settle to make the clause visible at all;
- `Divergence`'s empty-differences guard had no test, because nothing in the
  composed path can produce one;
- and the fixtures asserted nothing about what makes **ß** worth sending. Any
  letter round-trips; the property is that a case fold makes the string *longer*,
  and until that was asserted the character could be swapped for `s`.

**A mutation that survives is not always a defect.** Swapping ß for the ligature
ﬀ also survives, and should: ﬀ has the same property — one code point, two
characters when upper-cased — so the test is right to accept it. Pinning the exact
code point would test the fixture rather than the reason for it.

**Sabotage: 44 properties, all caught, zero skipped, after three survived.**
