# 12 — WHAT THIS CANNOT DO

**Read this before running anything.** Derived from `07-use-cases.md` §10, and
kept short on purpose: being clear here is more useful to you than a longer
feature list.

There are four kinds of limit, and they are not the same kind of thing. One is
physics, one is a decision, one is a boundary of what we can be pointed at, and
one is the current state of the work.

---

## 1. Not possible

| | Why |
|---|---|
| Reduce network round-trip latency | Physical. We reduce the *number* of round trips; their duration is not ours to change. |
| Fix noisy neighbours or host contention | We cannot construct a contrasting run, so we cannot measure it. |
| Change CPU quotas or infrastructure sizing | Not in the artifact we modify. |
| Rewrite your project in a faster language | A single hot path is reachable; a project is not. |
| Redesign your architecture | That is a decision about what the software should be, not an optimization. |

---

## 2. Deliberately refused

These are not gaps. They are categories where no verifier we can build makes the
change safe, so we diagnose and report and never patch.

**Concurrency and locking.** Our correctness check compares outputs for the same
inputs, and that cannot detect an introduced race: a race passes ten thousand
times and fails on the ten-thousand-and-first. We will tell you where contention
is, with the measurements. We will not touch it.

**Hard real-time systems.** Measurement-based analysis is insufficient for
worst-case execution time, and the failure mode is specific and nasty — a caching
optimization improves every metric we measure while degrading the worst case,
which is the only number that matters. Detected and declined.

**Third-party dependency code.** We report a cause inside a dependency. We do not
patch other people's packages.

**Production.** Test environments only, enforced by a database-URL check that
refuses to start rather than by a convention.

---

## 3. What we have to be given

- Source access
- The ability to run the project
- A throwaway database with realistic data
- Test-environment configuration

Not production credentials, not production data, not write access to your main
branch, not network egress.

**The data requirement is the one that blocks people.** A project with no
fixtures, no factories and no realistic seed data can sometimes be synthesized
from its schema and sometimes cannot. A measurement taken against unrealistic
data is a measurement of a different program, and we would rather refuse than
report one.

---

## 4. Where the work actually is, as of 2026-08-30

This section is the one that dates. It is written plainly because a capability
page that quietly overstates is worse than no page.

**Verified end to end.** A campaign assembles, binds a workload to a live Django
subject, drives it at three scales, fits growth per metric and reaches a
decision. The Epic 17 composition check does exactly that against a project with
a planted N+1 and finds it.

**Never done.** No run against a real subject with a live model. Every agent test
in this repository replays a recorded response; one spike aside, no model call
has been made. `coldfix run` says so and refuses, rather than letting you be the
first to find out.

**Frameworks.** Django is groundable end to end. Flask has a conforming adapter
and no grounding support, so it can be driven operation by operation and a
campaign will refuse it at fingerprinting — `coldfix plan` shows the difference.

**Caching is reachable, not measured.** The prefix is sent with breakpoints and
consecutive calls send a prefix the later one can read. What the API actually
served cannot be known from a replayed test, so `04-cost.md` §12.3's engineered
figure is a target rather than an achieved one.

---

## 5. Null results are a real answer

*Screened nine workloads and found nothing* is an output, not a failure. A tool
that always finds something is a tool that manufactures findings, and this one is
built — in schema, not in prompt — so that a conclusion without a measurement
under it cannot be expressed.

If we find nothing, that is what you will be told, along with what was screened,
at what scales, and under which fixtures. Exclusions carry their preconditions:
*not the database* is only true under the conditions that were tested, and those
conditions travel with the claim.
