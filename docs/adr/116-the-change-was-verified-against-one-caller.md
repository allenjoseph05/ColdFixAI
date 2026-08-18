# 116 — The change was verified against one caller

**Status:** accepted
**Story:** S-11.5 — scope attacks
**Date:** 2026-08-19

## Context

The whole investigation looked at one workload. The Diagnostician measured one
endpoint, the falsification test drives that endpoint, and S-10.4 confines the
patch to the files that endpoint's evidence implicates. Every one of those is a
statement about the same narrow slice.

The symbol the patch rewrote is called from wherever it is called from, and
nothing upstream has any reason to know where. This story asks the question none
of the others can: **the change was verified against one caller; who are the
others?**

## Decisions

### 1. `hunk_ranges` grew a `side`, rather than a second parser

S-10.5 reads **original-side** line numbers, because *did this attempt change the
same lines as the last one* is answered in the numbering two attempts have in
common. This story needs the opposite: the symbols are looked up in the **patched**
source, where the original numbering points at whatever the edit shifted — a hunk
inserting five lines above a method would find the method five lines short of
where it now is, and attribute the change to whatever used to be there.

The parameter went into `sandbox/patching.py` beside the existing parser rather
than into a new one here. Two implementations of *where does a hunk start* would
be two answers to a question with one right one, which is the same call S-11.3
made about `Cheat` and S-11.4 about `compare`.

### 2. Names, not bindings — wrong in both directions, and said so

Python resolves attributes at run time, so no static pass can say which
`to_representation` a call reaches. Matching by name is **over-inclusive** (an
unrelated class with a method of the same name is reported) and **still
under-inclusive** (`getattr(obj, name)()`, a dispatch table, a template, a signal
handler are all invisible).

Over-inclusive is the direction to fail in: a caller reported that turns out to be
unrelated costs a reader a glance, and one that is missed is the regression
shipping. Both directions are in `RESIDUE`, because *a short list is not evidence
that few things call this*.

An AST walk rather than a grep, so a docstring, a comment or a string containing
the name is not a caller. And **a name that is passed counts** —
`map(serializer.to_representation, rows)` breaks exactly as hard as a call, and a
pass collecting only `Call` nodes would report that file as untouched.

### 3. The suite runs on both revisions

A repository whose tests already fail — a stale snapshot, an unavailable service,
a flake — makes every patch look like it broke something, and one run against the
patched code cannot tell the two apart. `ALREADY_BROKEN` is a fourth outcome for
exactly that, and it establishes nothing in either direction rather than being read
as a failure the Surgeon should go and fix.

S-11.2's control against nondeterminism and S-11.3's against framework warm-up,
arriving a third time at the same shape: **the audit is worthless without the
original beside it.**

### 4. AC 3's *tested workload* is `scope_of`, reused

S-10.4 already answers *which files does this finding's evidence implicate*, and
uses it to confine the patch. Deriving a second notion of scope would let the patch
be confined by one definition and audited against another.

### 5. Innermost definition wins, and a decorator belongs to what it decorates

A change inside a method is attributed to the method, not to its class. And a
definition's range starts at its **first decorator**, not at its `def` line —
otherwise a change to `@functools.cache` falls outside every definition and is
filed as a module-level edit with no callers to find.

### 6. `clean` is a much smaller claim than *safe*

No caller outside the evidence, the suite still passes, everything the patch
touched was readable. Given decision 2, an empty `outside` is the absence of
evidence of other callers and not evidence of their absence.

## Consequences

**The survivor was a fixture shape, again.** Every source fixture called through an
attribute — `serializer.to_representation(book)` — so a sabotage that stopped
reading `ast.Name` callees changed no assertion. A module-level function called by
its own bare name is the shape that separates the two branches, and it took a new
fixture to reach it. Third epic story in a row to end on *the fixtures could not
discriminate*.

**A test's arithmetic was wrong and the code was right.** The new-side test
asserted that line 11 of a file grown by four blank lines would be
`BookSerializer.author_name`; it is `to_representation`. The lines were counted by
hand and the parser was not.

**Sabotage: 39 properties, all caught, zero skipped, after one survived.**
