# 156 — An uncertified speedup is unresolved, not a result

**Status:** accepted
**Date:** 2026-08-27

## Context

S-15.2 asks for a runner over a defined subset of SWE-Perf, compared against
expert patches, reported per category, with the subset size and selection
criteria stated openly.

The corpus is external — 140 instances with expert patches (arXiv:2507.12415) —
and running it costs an investigation per instance. So this is the harness on the
same terms as the other three `eval/` modules: it runs nothing, the observations
come from the harness, and the study can be re-run against recorded results
without paying for the corpus again.

## Decisions

### 1. An uncertified speedup is a third outcome

`05-research.md` §10.4 is the constraint and it is a finding about benchmarks of
exactly this kind. A 2026 audit of GSO, SWE-Perf and SWE-fficiency found that
runtime measurements are not fixed quantities — the same patch appears faster,
slower, or statistically unsupported depending on where it is replayed —
**despite those benchmarks already using repeated trials, outlier filtering,
statistical tests and reference patches**. The implication it draws is that a
harness must treat statistical certification as a first-class requirement rather
than a refinement.

So `Result` carries a `Certification` and `Standing` has four members.
`UNRESOLVED` is not a weaker loss: it says the instrument could not see an effect
the size of the one being claimed, so nothing about that instance was measured.

**Certification is checked before the numbers are compared at all.** A speedup
the harness could not resolve is not a small speedup; it is an unread instrument,
and comparing it against the expert's figure would be arithmetic over a number
nobody measured.

**The refusal is symmetric**, which is what makes it about the instrument rather
than about optimism: an uncertified *failure* is unresolved too. A rule that only
discounted the wins would be a different rule wearing this one's name.

**Unresolved instances are excluded from the rate, not counted against it.**
Counting them as failures says *we tried and did not match*, which is a claim
about the patch; what happened is that nothing was measured. A category where
nothing resolved has `matched_rate is None` rather than zero — the same refusal
`RunReport` makes about a run that confirmed nothing.

### 2. There is no aggregate, and no property that produces one

AC 3 says per category and not aggregate. A single rate over a corpus of unlike
instances averages measurements taken on different scales, which is
`08-audit.md`'s argument about ranking across kinds applied to scoring across
categories.

So `Benchmark` has no total. A test asserts the *absence* by inspecting the
public surface, because a test that only read the rendered report would pass for
a version carrying a rate nobody printed — and the first caller who wanted one
number would find it.

### 3. The categories are the dataset's

`Instance` takes the category it belongs to. This project has no opinion about
how SWE-Perf partitions its instances, and inventing a taxonomy here would
produce a per-category report about categories nobody else uses — the opposite of
*comparable against expert patches*. Same rule as the finding key in S-15.1: the
identifier is the caller's, and it has to come from outside.

### 4. `speedup=None` is not 1.0

*Nothing was proposed* and *something was proposed and changed nothing* share a
standing, because neither made the program faster. They are kept apart on the
artifact because a benchmark that recorded them alike would credit a system that
never answered with a null improvement.

### 5. What the run refuses

An unstated selection, because a subset whose criteria are unstated cannot be
told from one chosen after the numbers were seen — that is AC 4's whole point. A
subset larger than its corpus, because one of the two numbers is wrong and every
rate becomes unreadable. An instance scored twice, because it moves the rate for
a reason that is not about the system. And an expert patch that did not speed
anything up: a ratio of 1.0 makes every attempt a match and one below it makes
doing nothing a win.

## Consequences

**Epic 15 is complete as harnesses.** S-15.1, S-15.2, S-15.3 and S-15.4 all
exist; none of them has data, because the data costs runs. That is the shape the
no-spending rule has produced across four stories, and it is a good one: the
instruments were designed without knowing what the numbers would be, which is the
only order in which a refusal like §10.4's gets built at all.

**Sabotage: 3 properties, 3 caught** — scoring an uncertified result like any
other, counting unresolved instances against the rate, and adding an aggregate
rate to the benchmark.
