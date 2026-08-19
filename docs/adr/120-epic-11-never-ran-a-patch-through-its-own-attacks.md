# 120 — Epic 11 never ran a patch through its own attacks

**Status:** accepted
**Story:** Epic 11 composition check
**Date:** 2026-08-19

## Context

Eight stories: an isolated handover, five attacks, a verdict, an ablation. After
all of them the epic could not perform its own sentence — *defeat the patch, not
review it.* Five separate audits existed and nothing ran a single patch through
all of them.

**Fifth consecutive epic to end this way**, and the defect is the same shape every
time: a value one story produces and another consumes, where nothing in either
story's tests holds both ends.

## Decisions

### 1. Authorize, attack, record, then route

`route` asks *may another round start*. Asked **before** the round that just
happened is recorded, it says yes on the last permitted round — and a broken patch
goes back to a Surgeon whose reply nothing is left to audit.

The cap is checked in two places and only one of them moves the counter. S-11.1
wired `authorize_round` for *may this round start*; S-11.7's `route` calls it
again for a different question; `record_round` sits between them and is what
actually spends. Ordering is the whole fix.

### 2. One suite command, used twice

S-11.5 runs the suite with a command and S-11.7 builds the reproduction from an
argument of the same name. Two call sites, no shared value, and the failure is
silent: **the reproduction names a command that was never run**, and it is the one
thing a human is told to paste. `Subject` carries it once and `audit_patch` has no
parameter for it.

### 3. A strengthened test is re-gated before it is kept

`RegressionTest` refuses a proof of failure about a different test — S-10.3's *a
strengthened test is re-gated, not trusted*, at the artifact that ships. The only
`Falsified` in existence at this point is the one for the Surgeon's **original**
test, so on the branch where the audit strengthened something, `keep` could not be
called at all: **the artifact AC 3 exists for was unreachable.**

The replacement goes back through S-10.2's gate against the diagnostic worktree —
where a patch cannot exist — and then through S-10.6's `verify` against the
candidate. `None` is returned where the replacement passes on unpatched code:
that is `PASSED_UNPATCHED` reaching the permanent artifact, and shipping it would
install a regression test that can never fail.

### 4. Nothing can read a file out of a worktree

`03-agents.md` §6.2 lists `read_file(path)` among the Adversary's tools. No session
has it, and both `Candidate` and `ScopeAudit` need source. This module takes the
sources as parameters and names the absence rather than pretending a composition
can assemble them — `MISSING_READ_FILE` is in every report.

**This is a real dependency of Epic 12 on a capability nobody has built.** An
orchestrator is currently the only thing that could fill those arguments, and
nothing gives it the means either.

### 5. Two sessions, because two prompts

S-11.1's patch audit and S-11.6's test-quality audit have different system text.
One session reused for both bills and caches against a prefix that was never sent
— the defect Epic 10's composition found for the Surgeon.
`refuse_shared_session` catches it inside each `invoke`; passing two is what makes
it not arise.

### 6. `unattempted` exists so *nothing ran* cannot be spelled as an empty list

`verdict_for` refuses an empty list, so a caller that could not stand the subject
up needs a way to produce five `NOT_RUN` results. The verdict for that is
`suspicious` — never `clean`, which is the epic's recurring rule reaching the
composition.

## Consequences

**Three fixture limits masqueraded as defects and had to be told apart from them.**
The composition check's own scaffolding was wrong three times before the code was:

- the fake worktree recognised a suite command by `argv[0] == "pytest"`, so
  `python -m pytest` fell through to the probe branch and ran `--maxfail=1` as a
  program. Both revisions failed, the attack reported `ALREADY_BROKEN`, and the
  test that checks the reproduction never saw one. It now tells a probe from a
  suite by shape — `[interpreter, "-c", program]` — rather than by name;
- the cheat metrics supplied two names where five classes need five, so four came
  back `UNTESTED` and every round was `suspicious`. That is S-11.3 behaving
  exactly as designed and a fixture too thin to reach the clean path at all;
- the two worktrees held identical source, so no falsification test could fail on
  one and pass on the other. They now differ by an **added symbol** rather than a
  changed answer, so the equivalence attack still sees no difference and the clean
  path stays reachable.

**Source-normalising tooling rewrote `\n` escapes into real newlines**, breaking
string literals repeatedly. The scripts are built with `chr(10)` — the same remedy
S-11.2's unicode fixtures needed, for the same cause.

**Sabotage: 12 properties, all caught, zero skipped** — each of the four defects
restored one at a time, and each fails the composition check.
