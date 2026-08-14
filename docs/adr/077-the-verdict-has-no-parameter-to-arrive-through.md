# 077 — The verdict has no parameter to arrive through

**Status:** accepted
**Story:** S-7.8 — objective work verification (**SAFETY**)
**Date:** 2026-08-14

## Context

The story's *Why* is the design: **the agent is incentivized to claim success
because success completes its task.** An Explorer that has spent fifty steps
standing a repository up, getting past its login and seeding it has every reason
to report that the endpoint it finally reached does something — and no reason at
all to notice that it returns the same four hundred bytes whether the database
holds ten rows or ten thousand.

Four acceptance criteria: `work_verified` is computed by the harness; all three
metrics must move between N=10 and N=100 by stated thresholds; the agent cannot
override or supply the value; a workload failing verification is rejected
regardless of what the agent claims.

**Half of this already existed.** S-4.1 put `work_verified` on the artifact as a
property with no field behind it, and ADR 051 corrected F6's first condition. What
was missing was the half that produces the numbers it reads.

## Decision

### The signature is the enforcement

`verify_work` takes a path, an interpreter, a seeding plan and a credential.
**There is no argument through which a query count, a byte count or a duration
could arrive**, and `accept` takes exactly one parameter — the harness's own
measurements.

A gate that accepted a claim and ignored it would be one refactor away from
honouring it, so there is no `claimed`, no `override` and no `force`. A test
asserts the signature by inspection and fails the moment somebody adds one to
make a demo pass. This is the construction S-1.6 used when `compare()` was made
to accept callables only, and the fifth instance of the project's recurring
pattern: **make the unsafe state have no object to exist in.**

### The three metrics are measured in the subject's interpreter

Wall time and response bytes could be read from outside the process; **the query
count cannot** — nothing outside knows how many statements a request issued. One
program drives the request under Django's own `CaptureQueriesContext` and reports
all three together, which also keeps them describing the same invocations rather
than three separate ones.

Two details that are not incidental:

- **A warm-up request precedes the measured ones.** The first request through a
  Django stack pays module imports, template compilation and connection setup,
  and charging those to the small scale point is how a flat workload comes to
  look like a growing one.
- **The median of five, not one reading.** The wall-time condition is a ratio,
  and a single slow scheduling slice on the small point flips it. The samples
  travel beside the median, because a ratio between two medians is only as
  honest as the spread behind them.

### The sweep resets between scale points

Measuring N=100 on top of N=10 makes it a measurement of a hundred and ten, and
the growth it shows is arithmetic rather than a property of the workload. It is
also what lets the fixture keep the shape S-7.7 asked for.

### An error response is refused, not measured

An error page is cheap, constant and identical at every scale — exactly the
profile this check exists to reject. Allowing a workload to present as verified
by failing consistently would invert the whole test.

### F6's first condition is the corrected one

This story's acceptance criterion still carries the audit's original wording —
*query count … rise* — and ADR 051 established that this rejects every correctly
batched endpoint, which is the shape the tool exists to produce. The condition is
*queries did not fall*. The correction predates the criterion and the backlog
records why; nothing here restates the uncorrected version.

## Consequences

**The guarantee is the schema, not the type checker.** Attempting
`Workload(..., work_verified=True)` produces no mypy error at all: pydantic's
plugin does not model `extra="forbid"` as a signature, so the keyword
type-checks and fails only at runtime. Recorded in the test rather than papered
over with an ignore, because it is precisely `CLAUDE.md`'s point that a rule
which must hold needs enforcement in code rather than in a convention — and here
the enforcing code is `extra="forbid"`, not the annotation.

**The false negative is kept and named.** An aggregate endpoint does real work
and returns a fixed-size answer, which from outside is indistinguishable from a
stub. The evidence string says which two things it cannot tell apart rather than
calling the workload broken, and the test fixture includes one so that the
refusal is exercised rather than assumed.

**Makes easy.** S-7.9 gets a harness-computed `evidence_of_work` and an artifact
that already refuses to carry a claim. S-7.10's *never reports success when no
workload does real work* becomes a call to `accept`.

**Makes hard.** Verification costs two seeds, two resets and twelve requests, so
it is not something to run per candidate in a screening loop. S-7.3's ranking
exists to keep the number of candidates that reach here small.

**A residual risk, stated rather than solved.** The agent chooses *when* to call
this, and wall time is noisy near its threshold — so repeated attempts on a
borderline workload will eventually pass one. The median of five narrows the
window and S-7.10's step cap bounds the number of attempts, but nothing here
makes a borderline case deterministic. Worth revisiting if a real repository
produces one.

**Sabotage-verified on twenty-eight properties across three passes, all caught —
after two survived and three patterns never applied.** All six attacks on the
safety properties themselves were caught on the first pass: removing the gate's
check, giving it a `force` parameter, reading the verdict off the wrapper,
un-forbidding unknown fields on the artifact, un-freezing it, and making
`work_verified` return true unconditionally.

The two genuine survivors were both **weak or unreachable rather than wrong**:

- *A drive with no samples is accepted.* The guard was unreachable, because
  `repeats` was silently clamped to one. Clamping answers a question nobody
  asked, so it is now a refusal — and the malformed-answer branch is forced with
  `monkeypatch` at the subprocess boundary, S-7.2's rule for a branch the machine
  cannot reach naturally.
- *The warm-up is removed.* Nothing observed it, because removing a warm-up
  changes noise rather than correctness. The warm-up is now **timed and
  reported**: a field that disappears when the warm-up does, and a number worth
  reading in its own right when a subject's first request costs fifty times its
  second.

And one weak assertion, of the shape S-7.3 recorded: *`min <= seconds <= max`* is
satisfied by every sample, so a sabotage returning `samples[0]` walked straight
through the test that was supposed to establish the median. Real timings cannot be
made to disagree on demand, so three disagreeing samples are fed in at the
subprocess boundary — what is under test there is the arithmetic this module does
to an answer, not the answer itself.
