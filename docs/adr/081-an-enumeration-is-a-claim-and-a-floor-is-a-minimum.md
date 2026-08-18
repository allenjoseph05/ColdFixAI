# 081 — An enumeration is a claim; a floor is a minimum

**Status:** accepted
**Story:** S-7.12 — date-anchored environments
**Date:** 2026-08-14

## Context

ADR 010 already decided this: every environment is anchored to a date derived
from the repository's most recent commit, the resolver is constrained to that
date, the interpreter is read from the repository's own declarations, and the
anchor is recorded in the workload artifact and overridable.

The story's *Why* is the argument in one line: **a repository last touched in
2019 does not break because it is complex; it breaks because we hand it a 2026
toolchain.** Measured rather than asserted — `django>=2.0` resolves to **6.1**
today and to **2.1.4** as of a 2019 anchor.

Implementing it raised three questions ADR 010 did not have to answer.

## Decision

### An enumeration is a claim; a floor is a minimum

ADR 010 lists four places to read an interpreter from — `python_requires`, trove
classifiers, `tox.ini`, CI matrices — as though they were one kind of evidence.
They are two.

A classifier, a tox envlist and a CI matrix each **enumerate** versions the
project says it supports: *we test on 3.11* is a positive claim. `requires-python
= ">=3.8"` is a **floor**; it says 3.8 works and nothing whatever about 3.11.

Taking the floor as *the* version hands 3.8 to a project whose CI tests 3.11.
Taking it as a ceiling would be worse. So `Basis` records which was found, an
enumeration beats a floor, and the report says which it had rather than
presenting a minimum as a version the project was tested on.

**Versions are compared numerically.** `"3.9" > "3.10"` is true for strings and
false for Python, and a lexical maximum hands a project testing 3.9 through 3.12
the oldest of them.

### The committer date, not the author date

A patch written in 2015 and applied in 2019 was *resolved against* 2019's index
by whoever applied it. The author date would anchor the environment to a day the
code in this checkout never existed on.

### An override carries no commit, and no separate flag

`Anchor.overridden` is `commit is None`. A boolean beside the commit would be two
fields that could disagree about whether an override happened, and eventually
would. An override with no reason is **refused**: ADR 010 requires the override
precisely because a contemporary dependency may carry a since-fixed
incompatibility or a known vulnerability, and *why this run resolved against a
different date* is the whole value of recording it.

### A checkout with no history is refused, not dated today

A downloaded tarball is not a checkout. Defaulting to today would hand a 2019
repository a 2026 toolchain, which is the failure this exists to prevent.

### The anchor lives on `Workload`, not on the emission envelope

AC 4 says *the workload artifact*, and the reproducibility argument decides which
artifact that is. ADR 010: *anchoring makes the dependency set a recorded input
rather than a function of when the tool happened to run.* S-0.4's byte-identical
guard counters are void if a rerun silently resolves a different Django, so the
resolution inputs have to travel with the thing a later process reasons about —
which is `Workload`, not the envelope Epic 7 wraps it in.

`EnvironmentAnchor` is optional. `None` means **not recorded**, never *resolved
against today*: an absent anchor is a gap in the record, and the whole argument
is that resolving against today is what breaks a stale repository.

## Consequences

**Makes easy.** The two empty rows of S-0.3's recurrence matrix — *Python version
mismatch* and *dependency resolution failure* — are addressed before they are
ever populated. An abandoned repository becomes a different value of an existing
parameter rather than a new obstacle category.

**Makes hard.** AC 5 cannot be demonstrated without a real package index, so
these tests carry a new `index` marker, distinct from `slow` and `docker` for the
same reason those are distinct from each other: what they need is neither time
nor a local daemon.

**The bound is real and is carried in the code.** `Anchor.residue` states in
words that this covers the Python layer only — an old `psycopg2` needing a
`libpq` current Debian no longer ships is an operating-system problem with no
`--exclude-newer` equivalent. ADR 010 puts that residue in S-17.2's honest
limitations, and a test asserts the sentence travels with the anchor, because
*the environment is era-matched* is exactly the claim somebody would quote
without it.

**Rules out.** Resolving dependencies against "latest" and calling the result the
repository's environment.

**Sabotage-verified on nineteen properties across two passes, all caught — after
two survived and one pattern never applied.** Both survivors were **fixtures that
could not discriminate**, the shape now recorded four stories running:

- The committer-versus-author-date test set **both** git dates to the same value,
  so either field satisfied it. They are now four years apart.
- The CI decoys were `v4.2` and `timeout-minutes: 4.5`, which the version filter
  discards for an entirely different reason — so removing the key check changed
  nothing and the test passed against exactly the code it was written to reject.
  Every decoy is now a `3.x`.

The second is the sharper one, and it generalises past *make A and B disagree*: a
negative test needs a decoy the code would otherwise accept. A decoy rejected by
some other rule proves only that the other rule works.
