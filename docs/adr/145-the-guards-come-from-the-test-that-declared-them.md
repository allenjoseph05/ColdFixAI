# 145 — The guards come from the test that declared them

**Status:** accepted
**Date:** 2026-08-26

## Context

`adapters.ship` has said since S-12.7 that it does F14 and nothing else: *the
pull request is S-16.2, two epics away, and a stub here would be a second, worse
answer to a question another epic owns.* S-16.2 is that answer, and it is the
last thing standing between the pipeline and S-17.1's finding branch — the
holdout's expected result is a null one, but a run that *did* find something had
nowhere to put it.

**S-16.1 was already built.** `EvidenceChain.render` produces the symptom, the
mechanism, the localization, the growth table, the site, the implicated files and
every exclusion *with its preconditions*, and the preconditions property has been
tested first-class since S-8.6. Two assertions were added for the AC nothing had
covered; nothing was written.

## Decisions

### 1. The guards are read off the falsification test, never passed in

AC 1 asks for *guard metrics showing what did not regress*. The only honest
source is the test that declared them: `CostClaim.guards` is what S-10.1 required
non-empty, because *a cost claim with no guard is a test a cheat passes by moving
one number*.

A `pull_request` taking its own guard list could show a reviewer guards the test
never checked — a report of a check nobody ran. The signature has no parameter
with `guard` in its name, and a test asserts that by inspection.

### 2. A guard nobody measured after the patch is unverified, not satisfied

Three states, not two, and the third is the one a report gets wrong. `GuardReading`
distinguishes *checked and within its limit*, *checked and over it*, and *not
measured at all* — and the body says `NOT MEASURED … Unverified, not satisfied`
rather than rendering a row that reads like a pass.

This is the delta table's rule applied to guards: absence renders as absence,
because reading a missing measurement as satisfaction puts the most flattering
available answer under a reviewer's signature.

**Sabotage found this half-tested.** Making `held` answer `True` for an unmeasured
guard changed no test outcome, because the tests read `unverified_guards` and the
rendered text and neither goes through `held`. But `held` is the property a
caller branches on: `all(item.held for item in guards)` would have come back
green on a patch whose guards were never checked. Ninth time here that a
surviving sabotage was a missing test rather than a redundant guard.

### 3. The delta table has one owner

`gate.Approval._deltas` and the pull request show the same numbers. Two
renderings is how a gate report and a pull request come to disagree about what
improved, so `deltas` moved into `report/` and the gate renders through it.

**The first draft wrote a second `change()` and it was worse.** It rendered
`-87%` — correct, and a signed percentage leaves the reader to work out whether
down is good for this metric, which for half the metrics in the report (the
guards) it is not. The gate's existing wording — `87% better` — was already
tested and already right, so it moved rather than being rewritten. Reaching for a
new implementation when a tested one exists is the thing this project keeps
catching.

### 4. Round one's reproductions travel with round two's patch

S-11.7 returns a broken patch with something that can be run, and the Surgeon
tries again; the patch that ships is the survivor. A reviewer who can see what
the earlier attempt was caught by is better placed than one who sees only the
version that passed.

Empty is the ordinary case and renders as nothing — a heading with nothing under
it reads as a section somebody forgot to fill in.

### 5. A pull request is what a *cleared* patch gets

`pull_request` refuses any verdict but `clean`. S-11.7 routes `broken` back to the
Surgeon and `suspicious` to a human, and a body assembled from either would be
asking for a merge the audit declined to recommend, with the objection printed at
the top of it.

### 6. The suite result is read off the verdict, not supplied

S-11.5 runs the suite as an attack. A body quoting a separately-supplied result
could report a green suite the audit never saw. Where the attack did not run the
body says so, because an absent section reads as *nothing to report* — which for
a test suite is the most dangerous available reading.

## Consequences

- **S-17.1's finding branch has somewhere to go.** What remains blocking it is an
  API key and authorization to spend, not missing code.
- `report/` is a new package and S-16.3's null-result report belongs beside this.
- The regression test is proposed as a path rather than written to disk: the
  finding's id names the file, and this module does not get to decide a project's
  test layout.
- **Sabotage: 3 properties, 2 caught first time**, and the survivor is recorded in
  decision 2 above.
