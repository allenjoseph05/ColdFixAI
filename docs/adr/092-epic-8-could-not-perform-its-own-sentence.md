# 092 — Epic 8 could not perform its own sentence

**Status:** accepted
**Story:** Epic 8 composition check
**Date:** 2026-08-16

## Context

Nine stories, 295 tests in `tests/diagnosis/`, a sabotage pass on every one — and
no way to take a screened workload, investigate it, and emit an evidence chain.
The same finding Epic 7 recorded, and this epic repeats it almost exactly:
**every defect was a join**, and not one was a module wrong about its own
subject.

## The three defects

### 1. Two append-only logs, again — and this time across an epic boundary

`Session` constructs a `PrunedLog` and renders it into the block `04-cost.md` §4
caches. `ExperimentLog` wraps its own. Nothing joined them, so **the session's
log block rendered empty forever** while the real log rode in the uncached
question. Measured before the fix: two experiments in the loop's log, **zero
records in the session's**, and a 208-character log block containing nothing but
S-5.8's retrieval notice.

Epic 5's composition found this defect *inside its own epic* and recorded why it
is silent: caching is a prefix match, so a log wrong in content is still
append-only and still reports hits. What it costs is the entire growing part of
the prompt at full price on every call, against a cost model that assumes 85%
cached — and S-5.8's pruning report, which is where §5's 60–80% claim is
measured, was measuring an empty log.

S-8.4 exposed `.pruned` *for exactly this join* and nothing used it. One log, one
owner, one rendering.

### 2. The conditions and the symptom had no producer

S-8.5 requires fixture shape, platform, concurrency and scales on every
exclusion. Every caller built them by hand — **including every test**, which is
precisely why nothing noticed.

`Workload` already carries what is needed: `fixture.distribution` is the shape
the data was actually seeded at, and `observations` record the scales it was
actually driven at. A hand-built `Conditions` can say `uniform` while the recipe
says `long_tail`, and an exclusion recorded under a shape that was never used is
**permanently and wrongly live** — F3, reintroduced at the join S-8.5 exists to
close.

The symptom is the same shape one artifact over: `EvidenceChain` and
`PartialChain` both require one, and the investigation did not measure it — it
was handed it by screening.

### 3. S-8.6 was unreachable

A confirmed investigation had no path to the artifact the epic exists to produce.
`EvidenceChain` could be constructed by hand in a test and by nothing in the
system. That is Epic 7's *AC satisfied in isolation and unreachable in practice*,
and it is the more dangerous half of that pair, because **the criterion reads as
met**.

`chain_from` is the missing path. It deliberately does not invent a mechanism, a
site or the implicated files — those come from the agent and from S-3.9, and a
join that manufactured them to satisfy a constructor would be inventing the parts
of a finding that are hardest to check. It also refuses a confirming experiment
with no measured share, for the same reason.

## A defect found in this session and not fixed

`Session.run` assembles S-5.7's blocks — the cached prefix, the playbook, the
source, the log — and **the request never carries them.** Each diagnosis module
builds its own `messages=[{"role": "user", "content": question}]`, so the blocks
are computed for the viability report and discarded.

The consequence is that Epic 5's entire prompt-caching design is inert: the
`cache_control` breakpoints S-5.7 places are never sent, and whatever caching
happens is whatever the API does with an unstructured prompt.

This is **not fixed here**, and the reason is scope rather than doubt: fixing it
touches S-0.7b's client, `Session.run`, and all three Epic 8 call sites, and it
is a change to how every request in the system is shaped. It belongs in its own
story with its own sabotage pass. Recorded so that the next reader does not
rediscover it, and so that nobody quotes §12.3's per-finding cost as achieved.

## The test harness moved

`tests/fixtures/thesis.py` now holds the planted subject and the machinery that
drives it, because two suites need it and a test module importing another test
module is a source file mypy sees under two names. A second copy was the other
option and is the one this project keeps refusing.

The `query_counter` fixture registers under a name different from its function's,
so importing it does not read to a linter as a redefinition of the parameter that
uses it. The obvious route — a `tests/diagnosis/conftest.py` — collides with
`tests/sandbox/conftest.py` under mypy. **This project had already recorded that,
and this session walked into it anyway**, which is worth noting: a recorded
hazard is only useful if it is read before the obvious thing is tried.

## Sabotage

Ten properties, all caught — after one survived.

*The survivor is the eighth instance of the same shape.* Hardcoding `"uniform"`
in `conditions_for` changed nothing, because the subject's fixture **is**
uniform: a fixture where the right answer and the wrong answer coincide. The test
is now parametrised over every `Distribution`, so no shape can be the one that
happens to be checked.

The one worth keeping in mind for Epic 9: **restoring defect 1 is caught by two
tests, and neither of them is about logging.** They are about what the prompt
contains and what the run reports. A defect whose only symptom is a cost figure
needs a test that reads the cost figure.
