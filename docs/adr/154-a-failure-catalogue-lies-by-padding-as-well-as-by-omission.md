# 154 — A failure catalogue lies by padding as well as by omission

**Status:** accepted
**Date:** 2026-08-27

## Context

S-15.4 asks for a published catalogue of four kinds of negative result:
repositories where nothing was found, cheats that were caught with the diff and
the attack that caught them, diagnoses that flipped between runs, and groundings
that failed with the reason.

`00-BRIEF.md` §6 is the argument for it: *the failure catalogue is more credible
than the success rate. Publish it.* That claim is only true if the entries are
real, which is where the design work is.

All four sources already existed: `screening.null.NullResult`,
`audit.patchverdict.AttackResult`, `eval.agreement.Agreement` (S-15.1, landed
immediately before this) and `explorer.run.Failure`.

## Decisions

### 1. Every entry holds an artifact, not a sentence

Each of the four types produced its artifact under its own rules — a `NullResult`
cannot be constructed from a screen that flagged something, and an `AttackResult`
that landed cannot be constructed without the text a reader acts on. So a
catalogue entry is a reference to a measurement rather than a report of one, and
this module makes no claim of its own.

That is what makes AC 2 readable as written: *the Adversary caught three cheats*
is a claim; *here is the diff and here is the attack that caught it* is something
somebody can check. The diff is required and an empty one is refused.

### 2. Padding is a lie too, and it is the half nobody guards

Omission is the obvious failure and the reason the document exists. The other
direction is not obvious at all: **an entry recording a cheat nobody caught, or a
diagnosis that did not flip, is a failure that did not happen.**

A catalogue whose credibility comes from being uncomfortable is destroyed as
thoroughly by inventing discomfort as by hiding it — and the incentive runs the
wrong way, because a longer failure catalogue *looks* more honest. So
`CaughtCheat` refuses an `AttackResult` that did not land, and `FlippedDiagnosis`
refuses an `Agreement` that agreed.

`NOT_RUN` is refused alongside `PASSED`, and it is the quieter case: an attack
that could not see enough to answer is a gap in the audit, and recording it as a
catch turns a missing measurement into a success story about the Adversary.

### 3. An empty catalogue is the least credible artifact here, not the most

Empty means one of two opposite things: nothing has been run, or runs were
catalogued and none of them failed. The entries cannot distinguish those, so the
catalogue carries `runs_covered` — the same distinction S-4.5 draws between
*screened nine workloads, nothing found* and *nothing was screened*.

An empty catalogue therefore renders as **"Nothing was recorded across N run(s),
and that is not a result to be pleased about"**, naming both readings and saying
the catalogue cannot tell them apart. A catalogue over zero runs is refused
outright: it is not an encouraging result, it is not a result.

A section with no entries is absent rather than rendered empty, because an empty
heading reads as *we looked and found none* — a claim the catalogue is not in a
position to make per category.

## Consequences

**Epic 15 is complete except for the runs.** S-15.1 (agreement), S-15.2
(benchmark runner — still open), S-15.3 (cost report) and S-15.4 all exist as
harnesses; what none of them has is data, which costs investigations. The
no-spending rule has produced the same shape everywhere: the instrument is
buildable now and the number is for later.

**The holdout discipline test caught this story.** A fixture repository was named
`healthchecks-like`, which contains the holdout's name, and
`tests/test_holdout_discipline.py` failed the gate on it. The fix was to rename
the fixture — **never to widen the allow-list**, which is that test's own
instruction and the second time it has fired on a name that was not even a use.
It is worth recording that it fired on a *test fixture in a failure catalogue*:
the rule holds in places where naming the holdout feels harmless.

**Sabotage: 5 properties, 5 caught** — recording an attack that caught nothing,
recording a study that agreed, rendering the empty catalogue as good news,
accepting a catalogue over zero runs, and holding the diff without publishing it.
