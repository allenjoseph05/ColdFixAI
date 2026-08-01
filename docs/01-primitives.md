# 01 — PRIMITIVES

**The definitive set. Supersedes `capability-catalogue.md` entirely.**

Twelve primitives, assembled by validating against the established performance-analysis literature across three passes. Each pass found gaps: five, then three, then one.

---

## 1. The organizing principle

A primitive is **a way of constructing a contrast between two executions** — not a detector. Detectors have a ceiling equal to their list. Primitives compose, so the system finds things nobody enumerated.

Nine primitives vary something between two runs. One observes a single run. One compares a run to a model. Those are three distinct epistemic moves.

| # | Primitive | What varies |
|---|---|---|
| 1 | Scaling | input volume and shape |
| 2 | Load | concurrency |
| 3 | **Stress and recovery** | **load beyond capacity, then removed** |
| 4 | Longitudinal | elapsed time within one run |
| 5 | Temporal | code revision |
| 6 | Ablation | component presence |
| 7 | Proportional perturbation | component speed |
| 8 | Substitution | implementation or configuration |
| 9 | Platform | execution environment |
| 10 | Isolation | surrounding context |
| 11 | Observation | nothing — one run, counted |
| 12 | Bound comparison | nothing — run against a model |
| 13 | **Input space search** | **which input, not how much** |
| 14 | **Fault injection** | **dependency health** |

---

## 2. Scaling

**Method.** Run at increasing input size; fit each metric against size.

**Detects.** Cost that grows when it shouldn't: N+1 queries, quadratic accumulation, unbounded result sets, per-item allocation growth, missing pagination, per-row parsing.

**Vary two axes, not one.** Volume *and* shape. Uniform synthetic data hides skew-dependent defects at every scale — if every generated customer has three orders, an N+1 that only hurts customers with three thousand stays invisible. Generate power-law and long-tail distributions as well as uniform.

**Why it is the cheapest primitive.** Counts are deterministic. No warmup, no interleaving, no statistical test. The measurement-validity problems documented in `05-research.md` §10 do not apply to integers.

**Guard requirement.** Every metric pairs with the resource it can be traded against. `db.query` against `db.rows_returned`.

**Failure modes.** Framework-internal operations produce a constant offset — measure at N=0 and subtract. Clear caches between scale points. Force lazy results to materialize or the counter reads zero.

---

## 3. Load

**Method.** Hold data size fixed; increase concurrency. Fit throughput and latency against N.

**Detects.** Everything production engineers actually lose sleep over: connection-pool exhaustion, lock convoys, queueing delay, thread-pool saturation, tail-latency amplification.

**This is orthogonal to scaling.** A system can be flawless at 10⁶ rows single-user and collapse at 50 concurrent users with 10 rows.

**The model.** Gunther's Universal Scalability Law fits throughput X(N) with three coefficients:

- **γ concurrency** — ideal linear scaling
- **α contention** — queueing for shared resources; creates a horizontal throughput asymptote
- **β coherency** — delay for data to become consistent; makes throughput *decrease* past a peak

USL reduces to Amdahl's Law when β = 0. From a handful of measurements it yields **Nmax**, the useful concurrency ceiling.

**Why this is unusually valuable to an agent.** The coefficients are *diagnostic*, not just descriptive. High α points at a shared resource — the One-Lane Bridge antipattern. High β points at coordination cost. The fit tells the agent which hypothesis class to pursue next.

**Cross-check.** Little's Law (L = λW) relates throughput, latency and concurrency — measuring any two yields the third. Use it to verify measurements are self-consistent.

**Limitation.** Diagnose only. Contention fixes are refused (`00-BRIEF.md` §3).

---

## 4. Stress and recovery

**Method.** Push load past capacity, hold, then remove the stressor. Measure whether the system returns to baseline — and how long it takes.

**Detects.** Metastable failures: self-sustaining degradation that persists after the trigger is gone. Retry storms, death spirals, persistent congestion, queues that never drain.

**Why this primitive exists at all.** Metastable failures arise from *optimizations for the common case* that remove system slack. Named triggers include retries, **caching**, slow error paths, and load-balancer emergent behaviour. Systems run in a "vulnerable state" — working, but with no margin — often permanently, because it is more efficient.

**This is a direct threat from our own output.** Adding a cache, tightening a pool, removing redundant work — each reduces slack. A caching fix shows fewer queries, passes every other check, and can move a system from stable to vulnerable.

**Mandatory gate.** Any patch flagged `slack-reducing` (caching, memoization, retry logic, connection reuse, pool tightening — matched on the diff) must pass a spike-and-recovery test before it may be proposed, and always requires human review regardless of trust level.

**Practitioner equivalents.** Stress testing pushes beyond capacity to find the breaking point and observe how a system fails and recovers. Spike testing applies a sudden increase then decrease specifically to test recovery on return to normal.

**Failure mode.** Requires a load generator and a system that can be pushed past capacity safely. Not applicable to libraries or CLI tools.

---

## 5. Longitudinal

**Method.** Run the same workload at fixed size for an extended period. Fit metrics against *elapsed time* rather than input size.

**Detects.** The Ramp — processing time increasing as the system runs. Memory leaks, cache pollution, connection exhaustion, fragmentation, index bloat. Known in practice as soak or endurance testing.

**Real-world shape of the failure it catches:** error rates creeping over hours, then spiking overnight, from a leak that only surfaces after many hours of sustained traffic — while a thirty-minute load test the week before passed cleanly.

**Cost.** The most expensive primitive. Gate it behind evidence that a long-running process is the deployment model. Never run it on a CLI tool.

---

## 6. Temporal

**Method.** Check out earlier revisions; run the same measurement. Bisect.

**Detects.** Which commit caused a regression. Dependency upgrades that degraded things. Gradual accumulation where no single commit is responsible.

**Why it punches above its weight.** It requires no understanding of the code at all, is fully automatable, and produces the most actionable possible output: a specific commit and a specific number.

**Failure modes.** Older commits may not build or need different dependencies. The workload must exist and be runnable at both points in history.

---

## 7. Ablation

**Method.** Remove, stub, or short-circuit a component; measure the change.

**Detects.** The cost owned by any component — **whether or not an instrument exists for the resource it consumes.** Stub the serializer and the endpoint gets three times faster: the cost was in serialization, and no "serialization counter" was ever needed.

**This is the most important primitive.** It is resource-agnostic, which is how the system finds categories nobody anticipated.

**Use the algorithm, not guesswork.** Delta debugging solved the search problem. `ddmin` reduces a failing case to 1-minimal through successive testing; the stronger `dd` variant isolates the failure-inducing *difference* between a passing and a failing case. In Zeller and Hildebrandt's Mozilla study this reduced 896 lines of HTML to the single causative line in 139 automated runs — without any understanding of the input's syntax or semantics.

**Adaptation for performance.** Replace the boolean oracle ("does it crash?") with a threshold oracle ("does cost exceed X?"). Localizing among 40 candidates drops from ~40 ablations to ~6.

**Prefer `dd` over `ddmin`.** We usually have both a fast case and a slow case. Isolating the difference between them is exactly `dd`'s purpose.

**Critical structural rule.** Ablation deliberately breaks correctness. Ablation runs are **measurement-only** and must be structurally incapable of producing a shippable patch — separate container, separate worktree, destroyed on exit. Enforced by the harness, never by prompt.

---

## 8. Proportional perturbation

**Method.** Slow a component by a known fraction — or slow everything else — and measure the effect. Extrapolate the sensitivity curve.

**Detects.** What optimizing a component would *gain*, as opposed to what it *costs*.

**Why this is not ablation.**

| | Ablation | Proportional perturbation |
|---|---|---|
| Operation | remove entirely | slow by a fraction |
| Answers | what does it cost? | what would optimizing gain? |
| Correctness | broken | preserved |
| Concurrent systems | misleading — removing one bottleneck promotes the next | accurate |

**This is Coz's actual method,** which we cited for two passes without implementing. Coz virtually speeds up a line by inserting pauses in all concurrently-running code, measuring the causal effect of optimization. Its results: Memcached +9%, SQLite +25%, PARSEC applications up to +68%, mostly through fewer than 10 lines changed. The decisive datum — **the function responsible for SQLite's 25% gain accounted for ~0.15% of runtime.** Its worked example shows two functions with similar profile weight where optimizing one yields at most 4.5% and the other yields exactly zero.

**Rule.** Ablation for *localization*. Proportional perturbation for *prioritization*. One tool was doing two jobs.

---

## 9. Substitution

**Method.** Replace an implementation or configuration value; measure the delta.

**Detects.** Wrong data structure, slow serializer, inefficient query construction, general algorithm where a specialized one applies, interpreted loop replaceable by a vectorized call.

**Configuration is the highest-value sub-case.** Reversible, no syntax risk, no correctness risk from a malformed edit, bounded search space. A widely-cited figure attributes a majority of real performance problems to configuration rather than code. Targets: pool sizes, cache TTL and policy, batch sizes, prefetch depth, GC and heap parameters, timeouts and retry policy, compression, debug flags left enabled, index presence via query-plan comparison.

**Failure mode.** A substitution faster on the tested workload may be slower on another. Dev-environment results may not transfer to the production dialect or hardware.

---

## 10. Platform

**Method.** Hold code and input constant; vary the execution environment. Runtime version, CPU architecture, container versus host, allocator, OS version, filesystem.

**Detects.** "Slow only on the new ARM nodes." "Slow only after the runtime upgrade." "Slow in the container, fine on the host."

**Validity warning.** This is precisely the axis where measurement bias lives — environment variable size and link order alone can flip a conclusion, and a survey of 133 papers across four major conferences found none adequately accounting for it. Use setup randomization, or this primitive produces exactly the false results it is meant to detect.

---

## 11. Isolation

**Method.** Run a component alone, then in its normal context. The gap is interference.

**Detects.** Lock contention, pool exhaustion, cache thrash between components, resource starvation from background jobs, queue buildup.

**Standing restriction.** Diagnose only, never automatically fix. Output equivalence cannot detect an introduced race, so no falsification test could make a concurrency patch sound. This restriction is what allows the claim "faster without breaking anything" to be true.

---

## 12. Observation

**Method.** Attach a probe; count events and attribute them to call sites via captured stacks.

**Detects.** Duplicate queries, over-fetching, cache miss rates, chatty service calls, repeated file operations, allocation counts, bytes transferred.

**Two instrument classes, and the second is easy to forget:**

- **On-CPU** — instructions, allocations, calls
- **Off-CPU** — time spent *blocked*: disk, network, lock acquisition, scheduler queueing, GC pauses

Without off-CPU instrumentation, the entire **saturation** column of the USE Method is unmeasurable — and saturation is where bottlenecks announce themselves. An ablation without it tells you a component is expensive but never whether it computed or waited, and those have nothing in common as fixes.

**On instruction counting.** Tools like callgrind produce counts independent of machine and load. This makes CPU-bound optimization tractable without the measurement problems in `05-research.md` §10: search against instruction count, then validate the single winner with proper interleaved statistical timing.

**Localization via stacks.** Group events by normalized stack signature; strip framework-internal frames; walk to the divergence point. The frame below it is the causal site. This is how findings span multiple files without the agent reading the repo — the runtime names the files.

---

## 13. Bound comparison

**Method.** Compute a theoretical ceiling; compare the measurement to it. No second run.

**Detects.** Whether there is any headroom at all — *before* an investigation begins.

**The model.** Roofline bounds attainable performance by the minimum of peak compute throughput and peak memory bandwidth × arithmetic intensity. Below the ridge point a workload is memory-bound; above it, compute-bound. Kernels close to the roofline are already using the hardware well.

**Generalized as minimum necessary work:**

| Domain | Bound | Compare to |
|---|---|---|
| Data processing | bytes that must be read | bytes actually read |
| API endpoint | distinct entities required | queries issued |
| Serialization | fields consumed downstream | fields serialized |
| Network | payload semantically required | bytes transferred |
| Computation | instructions strictly required | instructions retired |

**Why it belongs in screening, not diagnosis.** A workload at 76% of bound has nothing left; one at 3% has thirty-fold available. Knowing this first is the cheapest way to avoid forty wasted experiments.

**Caveat.** Roofline is intentionally optimistic and does not account for non-overlapping bottlenecks across hierarchy levels. A bound is a ceiling, not a target.

---

## 14. Input space search

**Method.** Mutate inputs under a fitness function that rewards resource consumption. Keep the slowest, mutate again. Evolutionary search over input space.

**Detects.** Worst-case behaviour that average-case fixtures never trigger: regex catastrophic backtracking (ReDoS), hash-collision attacks, worst-case sort and hash-table inputs, deeply nested structure parsing, algorithmic complexity attacks generally.

**Why every other primitive misses this.** Scaling varies *how much* input. This varies *which* input. Algorithmic complexity vulnerabilities occur when worst-case complexity is far above average case **for particular user-controlled inputs** — so a system measuring average case with generated fixtures reports these programs as healthy.

**Established results.** SlowFuzz uses resource-usage-guided evolutionary search, is domain-independent, and achieved a 41.59× slowdown on insertion sort while triggering complexity vulnerabilities in the PCRE regex library, PHP's default hash table, and bzip2. PerfFuzz extends it with multi-dimensional feedback that independently maximizes execution counts per program location, escaping local maxima — exercising the hottest branch 5× to 69× more than prior work.

> Petsios, T., Zhao, J., Keromytis, A.D., Jana, S. "SlowFuzz: Automated Domain-Independent Detection of Algorithmic Complexity Vulnerabilities." *CCS 2017*. arXiv:1708.08437
> Lemieux, C., Padhye, R., Sen, K., Song, D. "PerfFuzz: Automatically Generating Pathological Inputs." *ISSTA 2018*.

**Security relevance.** ReDoS is a denial-of-service vector, not merely a slowness bug. Findings here may warrant different disclosure handling than ordinary performance findings.

**Cost.** The most expensive primitive by far — fuzzing campaigns run for hours. Gate it behind evidence that the target parses user-controlled input.

**Implementation note.** Do not write a fuzzer. Wrap AFL or an existing harness; PerfFuzz is built on AFL. The agent's contribution is choosing *what* to fuzz and interpreting results, not the mutation engine.

---

## 15. Fault injection

**Method.** Degrade a dependency — add latency, return errors, drop connections — and measure the effect on the system under test.

**Detects.** Misconfigured timeouts, retry logic that amplifies rather than recovers, missing fallbacks, cascading latency, degradation paths nobody designed.

**Why it is distinct from ablation.** Ablation removes *our own* component to measure its cost. Fault injection degrades something we *depend on* to measure our behaviour under partial failure. Different axis, different findings.

**Why one primitive covers a lot.** Netflix's Chaos Automation Platform focuses on two failure modes — a service becoming slower, or returning errors — because many distinct faults reduce to those two. Bad deploys look like a service returning errors; CPU, thread, memory and network-bandwidth exhaustion all look like a service slowing down.

> Basiri, A. et al. "Automating Chaos Experiments in Production." arXiv:1905.04648

**It partially rescues the metastability gate.** `08-audit.md` F1 downgraded the spike-and-recovery test as unexecutable in a single container. That remains true for full metastability, which needs scale. But **injecting latency into a dependency and observing whether retry logic amplifies load is executable**, and retries are the most commonly cited metastable trigger. Add this check to the slack-reducing gate: it does not prove safety, but it catches the common case.

**Blast radius rule.** Standard chaos practice is to start with the smallest possible scope and expand only after confirming safety controls work. In our setting the blast radius is already bounded — test environment only — but the same discipline applies to which dependency is degraded first.

**Scope.** Only meaningful for systems with external dependencies. Not applicable to libraries, CLI tools, or self-contained batch jobs.

---

## 16. LLM applications need their own instrument pack

The primitives apply. **The metric model does not**, and shipping the generic one produces confidently wrong answers.

LLM inference has two phases with opposite characteristics. **Prefill** processes the whole prompt in one parallel pass, is compute-bound, and determines Time To First Token. **Decode** generates one token at a time, is memory-bound, and determines Time Per Output Token.

Consequences that break our defaults:

- **Batching boosts decode throughput enormously but barely affects prefill.** One optimization improves one phase and can degrade the other.
- Decode's arithmetic intensity is so low that **one decode token costs roughly what 128 prefill tokens cost**.
- Standard metrics are TTFT, TPOT, Time Between Tokens, Time To Last Token — a single "latency" number is meaningless.

**Required changes:**

| Change | Why |
|---|---|
| Two independent scaling axes | input and output tokens behave oppositely; one axis draws wrong conclusions |
| Phase-decomposed timing | TTFT and TPOT separately; their mean is meaningless |
| Token counters | cost is per token — tokens are the natural counted resource |
| Cost as a first-class metric | uniquely here, money is directly measurable per request |
| Cache-hit rate | prompt caching changes cost by an order of magnitude |

**Assessment:** LLM applications are a *better than average* fit — cost is directly measurable and token counts are perfectly deterministic. They just need their own instruments.

---

## 17. Composition — where capability actually lives

Individual primitives find individual things. Compositions find what no single primitive would.

**Ablation → Observation → Scaling.** Stub the serializer: 88% of time was there. Attach the allocation counter to that path: 40 allocations per row. Scale it: both grow linearly. Conclusion with a measurement chain proving every link.

**Scaling → Ablation.** Query count flat, so not the database. Ablate each layer to find which owns the time.

**Load → Isolation → Substitution.** USL fit shows high contention. Isolate to find the shared resource. Substitute pool sizes to find the optimum.

**Temporal → Ablation.** Bisect to the offending commit, then ablate within that diff.

**Bound → everything.** Check headroom first; skip the workload if there is none.

**Composition cannot be pre-scripted, because each step's choice depends on the previous step's result. This is the concrete answer to "why does this need an agent."**

---

## 18. Honest completeness assessment

| Pass | Set | Gaps found |
|---|---|---|
| 1 | 6 → 8 | 5 |
| 2 | 8 → 11 | 3 |
| 3 | 11 → 12 | 1 |
| 4 | 12 → 14 | **2** |

**The rate is 5, 3, 1, 2 — not monotonic.** The pass-three claim that "the discovery rate is declining, which is evidence of convergence" was **premature and is withdrawn.** It rested on a single data point produced by a shallower search than passes one and two.

**The generalizable lesson:** a low discovery rate is as easily explained by a weak search as by genuine coverage. Search depth must be held constant before rate is evidence of anything.

**What still supports partial confidence:** the practitioner taxonomy — load, stress, spike, soak, volume, scalability, configuration, isolation — maps completely onto this set with nothing left over. That is real, but it only covers the *load-testing* family. Passes three and four both found gaps *outside* that family (metastability, input search, fault injection), which is precisely why an in-family completeness check was insufficient.

**What is honestly claimable:** broad coverage of *dynamic experimental* methods, assembled from four independent literature families, with no basis for claiming closure.

**What is not:** complete overall. Excluded by design and worth stating explicitly rather than hiding —

| Excluded | Why |
|---|---|
| Static and symbolic analysis | not experimental; useful for hypothesis generation only |
| Analytical queueing models | predicts behaviour at loads we do not generate |
| Program slicing, spectrum-based localization | designed for correctness faults |
| Hardware counter analysis | needs PMU access, usually unavailable in containers |
| Energy and power | different instrument class |

**Method for pass five:** look for techniques varying something not in the middle column of §1, and search *outside* the performance-testing literature — pass four's two finds came from security fuzzing and reliability engineering respectively, neither of which self-identifies as performance work. Remaining unexplored candidates: build and compiler configuration (PGO, LTO, optimization level), deliberately constructed aged internal state, thread-schedule exploration for concurrent programs, and instrumentation-overhead variation.

**Search discipline for whoever does it:** hold search depth constant with prior passes (at least four distinct queries across different literature families), or the resulting rate is not comparable.

**The claim to make:**

> Fourteen primitives, assembled by validating against canonical frameworks across four passes drawn from performance engineering, debugging theory, security fuzzing, and reliability engineering. Gaps found per pass: 5, 3, 1, 2. No convergence is claimed. Treat as current best coverage with a documented method for extending it.

Publishing the non-monotonic discovery rate is stronger than hiding it — it demonstrates the method is real rather than a post-hoc justification of a set someone picked first.
