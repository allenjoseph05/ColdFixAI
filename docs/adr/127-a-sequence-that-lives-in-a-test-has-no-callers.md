# 127 — A sequence that lives in a test has no callers

**Status:** accepted
**Date:** 2026-08-21

## Context

Every epic in this project ends with a composition check: one test that performs
the epic's own sentence once, because a suite where every file tests one import
will not tell you the parts fit together. Five consecutive epics have found a
real defect that way, and none of those defects was a module wrong about its own
subject — every one was a join.

The checks work. **What nobody noticed is where they left the sequence.**

S-12.7 set out to bind the orchestrator's seven nodes to the epics' entry points
and found there were five, not seven. Epics 9, 10 and 11 had put their
composition in `src/` — `audit/compose.py`, `repair/compose.py`,
`audit/patchcompose.py`. Epics 7 and 8 had put theirs in a test file and nowhere
else.

| Epic | The sequence | Where it lived |
|---|---|---|
| 7 grounding | fingerprint → … → emission | `tests/explorer/test_explorer_composed.py` |
| 8 diagnosis | loop → chain assembly | `tests/diagnosis/test_diagnosis_composed.py` |
| 9, 10, 11 | — | `src/` |

`tests/` is not importable from `src/`, so `ground` had nothing to call and
`investigate` could run S-8.9's loop but never produce the `EvidenceChain` that
three downstream nodes read.

## Decisions

### 1. The composition belongs in `src/`, and the check calls it

`explorer/compose.py` holds the sequence; `test_explorer_composed.py` calls
`ground_workload` instead of rebuilding it. **The test keeps every other test it
had** — the ones that pin each individual join — because those are what stop the
six defects the check found from coming back, and they need the pieces
separately.

Writing the sequence in the test first was right. It is how the six defects were
found, and a design that had gone straight to `src/` would have found them later
and more expensively. What was missing is the step after: **a composition check
that passes has produced a working sequence, and a working sequence with one
caller is a working sequence with no callers.**

### 2. A seventh join defect, invisible to the check that owned it

The check does this:

```python
resolution = resolve_auth(subject, python=..., path=path, request=requester(subject))
assert resolution.resolved, resolution.describe()
...
verification = verify_work(subject, python=..., path=path, ...)   # no headers, no cookies
```

`resolve_auth` mints a `Credential`. `attach` exists to turn one into the headers
and cookies a subsequent request carries. **Nothing called it.** The credential
was minted, asserted to exist, and dropped.

On this subject every route is open, so the dropped value changed no assertion —
which is exactly why it survived a check whose entire purpose is finding this
class. A route that actually required auth would have been minted a credential,
driven without it, and measured whatever a 401 costs: a real measurement of the
wrong thing, in a system whose first non-negotiable is *no finding without a
measurement*.

`carried()` is public rather than private, because it is the join and **a join
with no test of its own is how this one survived**. Its tests live in
`tests/explorer/test_compose.py` rather than beside the check, since they need no
repository and belong where they will actually be run — the check is `slow`.

Sabotage-verified: restoring the defect fails three tests.

### 3. The sequence refuses rather than guesses

`Plan` carries what the Explorer decides and the sequence will not infer. `entity`
is the sharpest of these and it is defect six of the original check: ranking
scores a mechanism by how well it seeds two scales, which is a property of the
mechanism and not of the workload, so two equally-good factories tie and the
tie-break is alphabetical. Composed, that chose `AuthorFactory` over
`BookFactory`, seeded a hundred authors, drove `/books/` and measured an empty
list — one query, thirteen bytes, every S-7.5 test passing.

Three stages stop the run rather than continuing with a value the previous stage
did not produce: an unsupported framework, no route that can be requested, and a
credential that could not be resolved. Each names the stage. **Stopping is an
answer** — `00-BRIEF.md` §9 — and S-7.11's own acceptance is that the Explorer
reports failure rather than claiming success on empty data.

### 4. `None` is *not recorded*, never *resolved against today*

`_environment` returns `None` where the caller named no requirements. S-7.12 kept
that distinction on `EnvironmentAnchor` and it survives here: a rerun that
silently resolved a different Django voids S-0.4's byte-identical guard counters,
and a field defaulted to today's index is indistinguishable from one that was
actually pinned.

## Consequences

`ground` now has an entry point. S-12.7 stays blocked on S-8.11, which is the
same problem in Epic 8 and wants the same remedy.

**The generalisable finding is about composition checks rather than about
grounding.** A check proves the parts fit; it does not put the assembled thing
anywhere the system can reach. Two of five epics ended with the sequence stranded
in a test, and neither `DONE` note records it — because from inside the epic
there was nothing left to do. The gap is only visible from the caller, which
arrives an epic or two later.

Worth adding to the composition-check habit: **when the check passes, ask where
the sequence now lives.** If the answer is *in the check*, the epic has one more
step to go.
