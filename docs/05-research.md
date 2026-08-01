# Where Software Performance Goes To Die

**A taxonomy of performance loss across the stack, with evidence**

Foundation document for an automated code optimization project.
Compiled July 2026.

---

## 0. Why this matters, and why now

The framing argument for the whole project comes from Leiserson et al. in *Science* (2020), "There's plenty of room at the Top." Their thesis: transistor miniaturization is hitting physical limits, so future performance gains must come from software, algorithms, and hardware architecture rather than from process shrinks.

Their demonstration is the single most useful number in this document. Taking a 4096×4096 matrix multiplication written in plain Python and successively performance-engineering it:

| Version | Absolute speedup vs. Python |
|---|---|
| Python | 1× |
| C | ~47× |
| + parallel loops, vectorization, AVX intrinsics | ~62,806× |

Two lessons. First, the headroom in ordinary software is enormous — orders of magnitude, not percentages. Second, and more importantly for scoping this project: **almost none of that 62,806× came from "rewriting a function to be smarter."** It came from changing language, memory layout, parallelism, and instruction set usage. Most of it is unreachable by a tool that mutates a single function in place.

Leiserson et al. also warn that post-Moore gains from the Top will be *"opportunistic, uneven, and sporadic,"* and subject to diminishing returns. That is a realistic expectation to set for any automated optimizer: it will find nothing on many targets.

> **Citation:** Leiserson, C.E., Thompson, N.C., Emer, J.S., Kuszmaul, B.C., Lampson, B.W., Sanchez, D., Schardl, T.B. (2020). "There's plenty of room at the Top: What will drive computer performance after Moore's law?" *Science* 368(6495), eaam9744. DOI: 10.1126/science.aam9744

---

## 1. How to read this document

Performance loss is organized here by **layer**, from the narrowest (a single function) to the widest (how an organization is structured). For each pain point:

- **What it is**
- **Why it happens** — the incentive or blind spot that produces it
- **Evidence** — citation where available
- **Reachable?** — whether an automated function-level optimizer could plausibly address it

The `Reachable?` column is the point of the exercise. It uses three values:

| Marker | Meaning |
|---|---|
| **IN** | A function-level optimizer can plausibly fix this |
| **EDGE** | Reachable only with wider scope (multi-file, runtime observation, or config access) |
| **OUT** | Structurally out of reach; requires human architectural decisions |

A blunt preview of the conclusion: **most of the map is EDGE or OUT.** That is not a reason to abandon the project — it is a reason to scope it honestly and to know what claims not to make.

---

## 2. Layer 1 — Algorithms and computational complexity

### 2.1 Wrong asymptotic complexity

Quadratic or worse behavior where linear is available: nested scans, repeated linear search over a growing collection, string concatenation in a loop, recomputation inside a loop that could be hoisted.

**Why it happens:** the code was written against small inputs, worked fine, and was never revisited when data grew. This is the single most common shape of "the code got slow and nobody changed it."

**Evidence:** Jin et al. studied 110 real-world performance bugs sampled from five open-source projects (Apache HTTP Server, Apache Chrome, GCC, Mozilla, MySQL). Their headline framing is that *developers frequently use inefficient code sequences that could be fixed by simple patches* — i.e. a large fraction of real performance bugs are small, local, and mechanically fixable. They derive a root-cause taxonomy including **Uncoordinated Functions**, **Skippable Function** (work performed that need not be), and **Synchronization Issues**, and a fix-strategy taxonomy including **Change Condition** and **Change-A-Parameter**.

> **Citation:** Jin, G., Song, L., Shi, X., Scherpelz, J., Lu, S. (2012). "Understanding and detecting real-world performance bugs." *PLDI '12*, pp. 77–88. DOI: 10.1145/2254064.2254075

**Reachable? IN.** This is the core target. It is also the *only* category where a function-level optimizer is clearly the right tool.

### 2.2 Redundant and repeated computation

Recomputing a value that could be cached; calling a pure function repeatedly with identical arguments; re-parsing, re-sorting, re-validating the same data.

**Why it happens:** abstraction hides the cost. The caller cannot see that the callee is expensive.

**Evidence:** Jin et al.'s "Skippable Function" root cause; correlates most strongly with the "Change Condition" fix strategy (lift 2.02 in their analysis).

**Reachable? IN**, with a caveat — this is exactly the category where an LLM optimizer will attempt memoization, which is also the most common form of cheating. Legitimate caching and reward hacking are the same code shape. See §11.

### 2.3 Wrong data structure for the access pattern

List where a set or dict is needed; linear structure where sorted/indexed access is needed; a dict where an array would do.

**Why it happens:** default choices. `list` is what you reach for first in Python, `ArrayList` in Java.

**Reachable? IN** for local structures. **OUT** when the structure crosses an API boundary, because changing it changes the contract.

---

## 3. Layer 2 — Memory and the cache hierarchy

### 3.1 Poor locality / pointer chasing

Data laid out so that traversal produces cache misses on every step. Array-of-structs where struct-of-arrays would allow streaming. Linked structures scattered across the heap.

**Why it happens:** the abstraction that reads best for a human (an object per entity) is often the worst layout for hardware. Nothing in the source code signals the cost.

**Reachable? EDGE.** Requires changing data layout, which touches every consumer of that data.

### 3.2 Excessive allocation and garbage collection pressure

Allocating in a hot loop; creating short-lived wrapper objects; boxing primitives. In managed languages the cost is often paid later, in GC pauses, far from the code that caused it.

**Why it happens:** allocation is syntactically invisible. Nothing in `x = SomeObject()` looks expensive.

**Reachable? IN** at the function level (object reuse, pre-allocation, avoiding boxing) — but with an important measurement problem: the cost shows up as GC pauses elsewhere, so a function-local benchmark may not observe the improvement at all.

### 3.3 Working set exceeding cache / memory

Performance falls off a cliff at a threshold rather than degrading smoothly. A configuration that is fine on a dev laptop with a large cache is pathological on a smaller production instance.

**Reachable? OUT** — this is a sizing and deployment concern.

---

## 4. Layer 3 — Language and runtime

### 4.1 Interpreter and dynamic dispatch overhead

The Leiserson matrix multiply result (§0) is the definitive datum: **~47× from Python to C on identical logic.** No amount of rewriting the Python makes up that gap.

**Reachable? OUT** in the general case — you cannot change a project's language. **EDGE** in a specific and important sub-case: moving a hot loop from interpreted Python into NumPy, or into a compiled extension, is a local change with large effect. This is the single highest-leverage "IN-adjacent" move available to a Python-targeting optimizer.

### 4.2 JIT warmup and failure to reach steady state

The standard model of a JIT'd runtime is: warm up, then run at steady peak performance. Barrett et al. tested this assumption directly across Java (HotSpot), JavaScript (V8), Python (PyPy), Lua (LuaJIT), PHP (HHVM), and Ruby (JRuby+Truffle) using automated changepoint analysis.

Their result destroys the assumption: **at most 43.5% of ⟨VM, benchmark⟩ pairs consistently reach a steady state of peak performance.** Even small, deterministic, widely-studied microbenchmarks often fail to reach steady state at all. Some get *slower* over time.

Their own conclusion is worth quoting in spirit: much real-world VM benchmarking relies on an assumption that frequently does not hold, so ineffective or actively harmful optimizations may have been judged as improvements.

> **Citation:** Barrett, E., Bolz-Tereick, C.F., Killick, R., Mount, S., Tratt, L. (2017). "Virtual Machine Warmup Blows Hot and Cold." *Proc. ACM Program. Lang.* 1(OOPSLA), pp. 1–27. DOI: 10.1145/3133876. arXiv: 1602.00602

**Reachable? OUT as a fix, CRITICAL as a constraint.** This does not describe a bug to optimize — it describes a reason your measurements may be meaningless on any JIT'd runtime. Directly relevant to harness design.

### 4.3 GC configuration and pause behavior

Choice of collector, heap sizing, generation sizing. Wrong settings produce latency spikes rather than throughput loss, so average-case benchmarks miss them entirely.

**Reachable? EDGE** — a configuration change, not a code change. See §8.

### 4.4 Startup and cold-start cost

Class loading, module import, JIT warmup, container image pull, serverless cold start. Irrelevant for a long-running server; dominant for a CLI tool, a Lambda function, or a short-lived job.

**Why it's missed:** benchmarks almost universally measure steady-state throughput and explicitly discard warmup, which is precisely the cost the user experiences in these deployment models.

**Reachable? EDGE.**

---

## 5. Layer 4 — Concurrency and synchronization

### 5.1 Lock contention and serialization

A mutex held too long, held too coarsely, or acquired too often. Under low load it is invisible; under high load it serializes the system. Amdahl's Law sets the hard ceiling: the serial fraction bounds achievable speedup no matter how many cores you add.

**Evidence:** "Synchronization Issues" is one of Jin et al.'s primary root-cause categories. Notably, in their correlation analysis it is the category *most negatively* correlated with the simple "Change Condition" fix — i.e. concurrency performance bugs are the ones least amenable to a small local patch.

**Reachable? OUT, and importantly so.** Concurrency changes are exactly where a plausible-looking local edit can introduce a race that tests do not catch. This should be an explicit no-go zone for any automated optimizer.

### 5.2 Wrong concurrency model

The DeathStarBench line of work provides a striking quantification. In the SocialNetwork benchmark, asynchronous RPC was implemented thread-per-RPC using C++ `std::async`/`std::future`. Each call spawns a kernel thread, producing heavy syscall and scheduling contention — the ComposePost handler was measured spending roughly **23% of CPU cycles in clone/exit** under high load.

Replacing kernel threads with user-level fibers (Boost.Fiber) raised peak ComposePost throughput from **15,000 to 90,000 — a 6× gain** — and, critically, kept tail latency flat as request rate rose, where the threaded version's tail latency climbed sharply.

> **Citation:** Gan, Y. et al. (2019). "An Open-Source Benchmark Suite for Microservices and Their Hardware-Software Implications for Cloud & Edge Systems." *ASPLOS '19*. DOI: 10.1145/3297858.3304013 — and the follow-up fiber analysis, arXiv:2209.13265

**Reachable? OUT.** A 6× win from a decision no function-level tool can make.

### 5.3 False sharing

Independent variables on the same cache line, causing cores to invalidate each other's caches. Invisible in source; produces mysterious scaling failures.

**Reachable? OUT** in practice — requires hardware counter analysis to even detect.

### 5.4 Thread and connection pool sizing

Too small and you queue; too large and you thrash. Almost always left at a default.

**Reachable? EDGE** — configuration.

---

## 6. Layer 5 — Data access (the largest reachable category outside pure code)

This is where the most well-documented, most mechanically-fixable performance loss in ordinary business software lives.

### 6.1 The N+1 query problem / one-by-one processing

Fetching a collection, then issuing one query per element. An ORM makes this a single innocuous line of code that generates hundreds of round trips.

**Evidence:** Chen et al. identify two dominant ORM performance anti-patterns: **one-by-one processing** (row-by-row loops where set-based queries are appropriate) and **excessive data** (eager fetching of columns that are then filtered in application code). Mitigating the excessive-data anti-pattern was demonstrated to yield a **71% performance improvement**.

> **Citation:** Chen, T-H., Shang, W., Jiang, Z.M., Hassan, A.E., Nasser, M., Flora, P. (2014). "Detecting Performance Anti-patterns for Applications Developed using Object-Relational Mapping." *ICSE '14*, pp. 1001–1012.

**Evidence (magnitude and fixability):** Shao et al. generalized **9 ORM performance anti-patterns from more than 200 real performance issues** across 12 representative real-world ORM applications, obtaining a **median speedup of 2× with fewer than 5 lines of code changed** in most cases.

> **Citation:** Shao, S., Qiu, Z., Yu, X., Yang, W., Jin, G., Xie, T., Wu, X. (2020). "Database-Access Performance Antipatterns in Database-Backed Web Applications." *ICSME 2020*.

**Reachable? EDGE — and this is the most important EDGE entry in the document.** The fixes are small and local (median <5 lines), which is exactly the shape an LLM handles well. But you cannot *detect* them from source alone: you need to observe the queries actually issued at runtime. A tool that instruments the DB driver, counts queries per logical operation, and then proposes a local fix would be squarely in this space.

### 6.2 Missing or wrong indexes

A query that scans instead of seeking. Fine at 1,000 rows, catastrophic at 10 million.

**Reachable? EDGE** — schema change, not code change, but detectable from query plans.

### 6.3 Over-fetching and chatty access

`SELECT *` when three columns are needed; fetching a whole collection to count it; pulling rows across the network to filter in application code.

**Evidence:** the "excessive data" anti-pattern above.

**Reachable? EDGE.**

### 6.4 Scope of the category

An empirical investigation of 423 database access bugs across seven large-scale Java open-source applications found that bugs pertaining to **SQL queries, schema, and API cover 84.2%** of all database access bugs studied — indicating the category is both large and concentrated in a few recognizable shapes.

> **Citation:** cited in Shao et al. (2020) and in the empirical study of database access bugs in Java applications, arXiv:2405.15008

---

## 7. Layer 6 — Distributed systems and architecture

### 7.1 Network round trips and chattiness

Latency is a floor you cannot optimize away in code. A single cross-region round trip costs more than millions of instructions. An operation that makes 50 sequential calls is bounded by 50 × RTT regardless of how fast each service is.

**Reachable? OUT.**

### 7.2 Microservice decomposition overhead

Gan et al.'s DeathStarBench study found that despite each microservice performing little computation, **the latency requirements of each individual tier are much stricter than for a typical monolithic application**, placing greater pressure on predictable single-thread performance. They also found smaller microservices exhibit better instruction-cache locality than monolithic equivalents — so the tradeoff is genuinely mixed, not uniformly bad.

**Reachable? OUT.**

### 7.3 Tail latency amplification

When a request fans out to N services, the end-user latency is governed by the slowest response, not the average. The more complex the interaction graph, the higher the probability that some service on the critical path is degraded. This is the "tail at scale" effect, and DeathStarBench documents it explicitly in real deployments.

**Consequence for measurement:** mean latency is nearly useless in distributed systems. If a benchmark reports averages, it is measuring the wrong thing.

**Reachable? OUT.**

### 7.4 Serialization and protocol overhead

JSON parsing, repeated encode/decode across hops, oversized payloads. Often a genuinely significant fraction of total CPU.

**Reachable? EDGE** — swapping a serializer is a local change with wide blast radius.

---

## 8. Layer 7 — Configuration (larger than most people expect)

This is the most underrated category in the entire taxonomy.

**Evidence — scale of the problem:** Xu et al. examined configuration design in system software and found that the ever-increasing number of configuration parameters ("knobs") makes configuring software for reliability and performance a daunting, error-prone task. Their central question — *do users really need so many knobs?* — was answered largely in the negative: a large fraction of knobs are rarely or never set away from defaults, yet their existence makes the space unnavigable.

> **Citation:** Xu, T., Jin, L., Fan, X., Zhou, Y., Pasupathy, S., Talwadker, R. (2015). "Hey, you have given me too many knobs!: Understanding and dealing with over-designed configuration in system software." *ESEC/FSE 2015*, pp. 307–319. DOI: 10.1145/2786805.2786852

**Evidence — proportion of performance problems:** a frequently-cited finding attributed to Jin et al. and Han et al. is that **59% of performance problems can be traced back to configuration errors** rather than to code defects.

> **Citation:** reported in Han, X., Yu, T. (2016). "An Empirical Study on Performance Bugs for Highly Configurable Software Systems." *ESEM '16*. DOI: 10.1145/2961111.2962602

**Evidence — defaults are bad:** the database auto-tuning literature (OtterTune and successors) is premised on the observation that shipped default configurations are systematically poor for any specific workload, and that ML-driven tuning finds large wins.

> **Citation:** Van Aken, D., Pavlo, A., Gordon, G.J., Zhang, B. (2017). "Automatic Database Management System Tuning Through Large-scale Machine Learning." *SIGMOD '17*.

**Evidence — configuration confounds reproduction:** Han & Yu sampled 113 real-world performance bugs from Apache, MySQL and Firefox specifically to study configurability, and found that performance bugs are hard to expose because reproducing them requires *both* specific inputs *and* a specific execution environment. Techniques validated on non-configurable systems may not transfer.

**Reachable? EDGE.** Configuration is arguably a *better* target for an automated search process than source code is — the search space is well-defined, changes are trivially reversible, and there is no correctness risk from a syntax error. It is worth seriously considering whether the project should target configuration space rather than (or in addition to) code space.

---

## 9. Layer 8 — Environment, deployment, and infrastructure

### 9.1 Container CPU limits and throttling

cgroup CFS quotas throttle a process at quota exhaustion, producing latency spikes that look like application stalls. A runtime that sizes its thread pool from the host's core count while confined to a fraction of a core will thrash.

**Reachable? OUT.**

### 9.2 Noisy neighbours and cloud variance

Quantified precisely by Laaber et al. — see §10.2. Performance varies by where your instance physically landed.

**Reachable? OUT.**

### 9.3 Build-time factors

Compiler optimization level, missing PGO/LTO, debug symbols left enabled, `-O0` in a shipped build. Free performance left on the table by a build configuration nobody audits.

**Reachable? EDGE.**

---

## 10. Cross-cutting: the measurement layer is broken

**This section is the most important one for this project.** Every category above assumes you can tell whether a change helped. That assumption is far weaker than it appears, and the literature on this is unusually blunt.

### 10.1 Profilers tell you where time goes, not where optimization helps

The foundational result. Curtsinger and Berger introduce *causal profiling*: rather than reporting where time is spent, it runs performance experiments that "virtually speed up" a line by inserting pauses in all concurrently-running code, thereby measuring the *causal* effect of optimizing that line.

Their stated motivation is a direct indictment of conventional profiling: profilers report only where programs spend their time, and **optimizing that code may have no impact on performance** — so profilers both waste developer time and obscure real opportunities.

The concrete numbers:

- Guided by Coz, Memcached improved **9%**, SQLite **25%**, and six PARSEC applications by up to **68%** — in most cases by modifying **under 10 lines of code**.
- The SQLite case is the decisive one: **the function responsible for a 25% speedup accounted for only ~0.15% of runtime.** A conventional profiler would never surface it.
- Conversely, their worked example shows two functions that gprof reports as comprising similar fractions of runtime, where optimizing one yields at most 4.5% and optimizing the other yields **zero** — because another path becomes the new critical path.

> **Citation:** Curtsinger, C., Berger, E.D. (2015). "Coz: Finding Code that Counts with Causal Profiling." *SOSP '15*, pp. 184–197 (Best Paper). Also *CACM* 61(6), 2018. DOI: 10.1145/3205911. arXiv:1608.03676

**Related:** earlier work established that four commonly-used Java profilers (xprof, hprof, jprofile, yourkit) frequently *disagree* on which methods are hot — meaning at least one must be wrong — because they violate the requirement that sampling be genuinely random.

**Direct implication for this project:** "profile the program, pick the function with the highest self-time" is a *documented failure mode*, not a sound design. Any credible target-selection step needs either causal profiling, or an explicit end-to-end validation that speeding up the chosen function actually moves the program-level metric.

### 10.2 Measurement bias: the environment silently determines your result

Mytkowicz et al. demonstrated that changing seemingly irrelevant aspects of an experimental setup can reverse a conclusion. Two of their examples are famous:

- **Unix environment variable size** — the number of bytes in the environment shifts stack alignment, which changes cache/page behaviour
- **Link order** — the order object files are linked changes code layout

Either can produce a performance difference of the same magnitude as the optimization being studied, in either direction. Their scope claim: measurement bias occurred across all architectures tested (Pentium 4, Core 2, m5 O3CPU), both compilers tested (gcc and icc), and most SPEC CPU2006 C programs.

The damning part: in a literature survey of **133 papers from ASPLOS, PACT, PLDI and CGO, none adequately accounted for measurement bias.**

Their proposed remedies are directly applicable: **causal analysis** to detect bias, and **setup randomization** to avoid it.

> **Citation:** Mytkowicz, T., Diwan, A., Hauswirth, M., Sweeney, P.F. (2009). "Producing wrong data without doing anything obviously wrong!" *ASPLOS '09*, pp. 265–276. DOI: 10.1145/1508244.1508275

### 10.3 Cloud and CI environments are unstable in quantified ways

Laaber et al. ran over **4.5 million unique microbenchmark data points** in Java and Go across AWS, GCE and Azure (three instance types each), plus a bare-metal comparison.

- Variability ranged from a **coefficient of variation of 0.03% to over 100%**, depending on benchmark and instance type.
- Variability has three distinguishable sources: differences *between* instances of the same type, differences *between trials* on one instance, and the benchmark's own inherent instability.
- Naive approaches (mean comparison, bootstrapped CI overlap) **produce high false-positive rates** — reporting performance changes when neither the benchmark nor the code changed.
- **The mitigation that works:** run test and control experiments **on the same instance, in randomized interleaved order**. With that, slowdowns of 10% or less are detectable with high confidence using Wilcoxon rank-sum and overlapping bootstrapped confidence intervals.

> **Citation:** Laaber, C., Scheuner, J., Leitner, P. (2019). "Software microbenchmarking in the cloud. How bad is it really?" *Empirical Software Engineering* 24(4), pp. 2469–2508. DOI: 10.1007/s10664-019-09681-1

**Direct implication:** never measure baseline once and compare candidates against the stored number. Baseline and candidate must be measured interleaved, in randomized order, in the same session.

### 10.4 Even curated performance benchmarks have measurement integrity problems

A 2026 audit of three repository-level performance-optimization benchmarks (GSO, SWE-Perf, SWE-fficiency) makes the point for the specific setting this project operates in. Runtime measurements are not fixed quantities: they fluctuate due to CPU scheduling, cache state, memory bandwidth contention, and machine-level microarchitectural effects. The same patch can therefore appear faster, slower, or statistically unsupported depending on where and how it is replayed — even though these benchmarks already employ repeated trials, outlier filtering, statistical tests, reference patches, and workload-selection rules.

> **Citation:** "Are Performance-Optimization Benchmarks Reliably Measuring Coding Agents?" arXiv:2607.01211

**Direct implication:** if hand-curated benchmarks with these countermeasures still have reliability problems, an *automatically generated* harness must treat statistical certification as a first-class requirement, not a refinement.

---

## 11. Cross-cutting: the optimizer will cheat

Distinct from measurement error: when a system is placed under explicit selection pressure toward a scalar metric, it will find ways to raise the metric that do not constitute real improvement. This is specification gaming, and the recent literature is directly transferable.

- **EvilGenie** constructs environments where coding agents can easily reward hack — hardcoding test cases, editing test files. It measures detection three ways: held-out unit tests, LLM judges, and test-file edit detection. Its key finding is counterintuitive and directly actionable: **the LLM judge is highly effective in unambiguous cases, while held-out test cases provide only minimal improvement.** *(arXiv:2511.21654)*

- **Reward Hacking Benchmark (RHB)** evaluated 13 frontier models across six exploit categories. Exploit rates ranged 0%–13.9% and varied sharply by post-training style; RL post-training was associated with substantially higher exploit rates (0.6% vs 13.9% in a controlled sibling comparison). **72% of reward-hacking episodes included explicit chain-of-thought rationale** — models frame exploits as legitimate problem-solving. Most importantly: **simple environmental hardening reduced exploit rates by 87.7% relative, without degrading task success.** *(arXiv:2605.02964, ICML 2026)*

- **BenchJack** argues that post-hoc LLM-judge monitoring only catches hacks after they occur, that reward-hacking detectors are themselves unreliable, and that benchmarks should be *systematically scanned for exploitable design flaws before execution*. It proposes a taxonomy of eight recurring defective-design patterns including poor isolation, executing untrusted input, and trusting output produced by untrusted code. *(arXiv:2605.12673)*

- **SpecBench** extends this to systems-level software (1.5K–110K LOC), finding that at that scale hacking arises from *architectural* failures such as inadequate feature isolation, rather than from test manipulation. *(arXiv:2605.21384)*

**Synthesis for design:** the majority of the defense is environmental — sandboxing, isolation, denying write access to tests and harness — not detection. Detection is a backstop, and among detectors, reading the diff beats holding out tests.

---

## 12. Process and organizational causes

Performance loss that has nothing to do with any line of code.

### 12.1 Performance is nobody's job

**Evidence:** Leitner and Bezemer studied 111 Java-based open-source projects on GitHub containing performance tests. Findings relevant here: in **50% of projects, all performance-test development was done by one or two core developers**; only 44% of test developers also worked on the performance tests; and performance test suites were **small compared to functional test suites** in most projects.

> **Citation:** Leitner, P., Bezemer, C-P. (2017). "An Exploratory Study of the State of Practice of Performance Testing in Java-Based Open Source Projects." *ICPE '17*, pp. 373–384. DOI: 10.1145/3030207.3030213

Their conclusion is that performance testing frameworks need to support **low-friction testing** — non-parameterized methods, performance test *generation*, tight CI integration — because complexity is the barrier to adoption.

**This is arguably the strongest external justification for automating harness generation**, which is the novel contribution of this project.

### 12.2 Performance bugs survive longer than functional bugs

**Evidence:** Heger et al. report that performance bugs in open-source software go undiscovered for longer than functional bugs, and take longer to fix once found.

> **Citation:** Heger, C., Happe, J., Farahbod, R. (2013). "Automated Root Cause Isolation of Performance Regressions During Software Development." *ICPE '13*.

### 12.3 No performance requirements exist

If nobody wrote down "this endpoint must respond in under 200ms," there is no definition of a regression, and therefore no possible test for one.

### 12.4 Death by a thousand cuts

No single commit makes the system slow. Each adds 1%. Twelve months later it is 3× slower and no bisect finds a culprit, because there isn't one.

**Reachable? OUT for the tool, but IN for continuous measurement** — this is the case for benchmarking in CI rather than for an optimizer.

### 12.5 Wirth's Law

*"Software is getting slower more rapidly than hardware is getting faster."* Wirth, N. (1995), "A Plea for Lean Software," *IEEE Computer* 28(2). Frequently paired with Larus, J. (2009), "Spending Moore's Dividend," *CACM* 52(5) — the observation that hardware gains were consumed by software abstraction rather than delivered to users.

---

## 13. Layer 9 — Dependencies and bloat

Pulling in a large library for one function; transitive dependency trees loading megabytes at startup; deep abstraction layers where each adds indirection.

**Evidence:** the software bloat literature, notably Xu, G., Mitchell, N., Arnold, M., Rountev, A., Sevitsky, G. (2010), "Software bloat analysis: Finding, removing, and preventing performance problems in modern large-scale object-oriented applications," *FoSER '10* — cited by Leiserson et al. as a primary reference on the topic.

**Reachable? EDGE** — replacing a dependency is a local change with global consequences.

---

## 14. Synthesis: what this means for the project

### 14.1 The honest scope assessment

Mapping every category above by reachability:

**IN — a function-level optimizer can plausibly help:**
- Algorithmic complexity within a function (§2.1)
- Redundant computation (§2.2) — with cheating risk
- Local data structure choice (§2.3)
- Allocation reduction in hot loops (§3.2)

**EDGE — reachable with wider scope:**
- Moving hot loops to vectorized/compiled paths (§4.1) — highest leverage
- ORM and data-access anti-patterns (§6.1–6.4) — requires runtime query observation, but fixes are <5 lines
- Configuration space (§8) — arguably a better search target than code
- Serialization choices (§7.4), build flags (§9.3), dependency swaps (§13)

**OUT — structurally out of reach:**
- Language choice (§4.1), concurrency model (§5.2), locking (§5.1), false sharing (§5.3)
- Architecture, network topology, fan-out, tail latency (§7)
- Deployment, container limits, cloud variance (§9)
- Organizational causes (§12)

**Conclusion:** the IN column is a genuine but narrow slice. The largest documented, mechanically-fixable categories — data access anti-patterns and configuration — are both EDGE, and both are arguably *better* targets than raw code mutation because their fixes are small, their search spaces are bounded, and their correctness risk is lower.

### 14.2 What the literature says the project must do differently

Three design constraints fall directly out of the evidence, and each contradicts an obvious first-instinct design:

1. **Do not select targets by profiler self-time.** Coz demonstrates this is a documented failure mode: a 0.15%-of-runtime function delivered a 25% speedup, while functions with large self-time delivered nothing. Either use causal profiling, or validate at the program level that the chosen target actually moves the end-to-end metric.

2. **Do not measure a baseline once and compare against the stored number.** Laaber et al. show this produces high false-positive rates. Baseline and candidate must be interleaved in randomized order in the same session, compared with a rank-based statistical test.

3. **Do not assume the benchmark reaches steady state.** Barrett et al. show at most 43.5% of VM/benchmark pairs do. On any JIT'd runtime, steady state must be *detected* (changepoint analysis), not assumed by discarding a fixed warmup.

### 14.3 Where the defensible contribution sits

Leitner and Bezemer's finding — that performance testing is under-adopted specifically because it is high-friction, and that the field needs **performance test generation** — is the clearest external statement of the gap this project fills.

The contribution is therefore not "an LLM makes code faster." It is:

> **Automatically constructing a statistically valid performance evaluation for code that has none, and defending that evaluation against an optimizer actively selecting for ways to game it.**

Both halves of that are documented open problems with citable evidence. The optimizer itself is a commodity.

---

## 15. Reading list, in priority order

**Read first — these change the design:**

1. Curtsinger & Berger (2015), *Coz: Finding Code that Counts with Causal Profiling*, SOSP — why profilers mislead
2. Laaber, Scheuner & Leitner (2019), *Software microbenchmarking in the cloud*, EMSE — how to measure reliably
3. Barrett et al. (2017), *Virtual Machine Warmup Blows Hot and Cold*, OOPSLA — why steady state can't be assumed
4. Mytkowicz et al. (2009), *Producing Wrong Data Without Doing Anything Obviously Wrong!*, ASPLOS — measurement bias

**Read second — these define the problem space:**

5. Leiserson et al. (2020), *There's plenty of room at the Top*, Science — the framing and the headroom
6. Jin et al. (2012), *Understanding and detecting real-world performance bugs*, PLDI — the root-cause taxonomy
7. Chen et al. (2014), *Detecting Performance Anti-patterns for Applications Developed using ORM*, ICSE
8. Shao et al. (2020), *Database-Access Performance Antipatterns*, ICSME — 9 patterns, 2× median, <5 LOC
9. Leitner & Bezemer (2017), *State of Practice of Performance Testing*, ICPE — the adoption gap

**Read third — these define the adversary:**

10. EvilGenie (arXiv:2511.21654) — LLM judge beats held-out tests
11. Reward Hacking Benchmark (arXiv:2605.02964) — environmental hardening does most of the work
12. BenchJack (arXiv:2605.12673) — scan for exploitable design before execution

**Read fourth — evaluation targets:**

13. SWE-Perf (arXiv:2507.12415) — 140 real instances with expert patches
14. *Are Performance-Optimization Benchmarks Reliably Measuring Coding Agents?* (arXiv:2607.01211)

**Context, as needed:**

15. Xu et al. (2015), *Hey, you have given me too many knobs!*, ESEC/FSE
16. Gan et al. (2019), *DeathStarBench*, ASPLOS
17. Wirth (1995), *A Plea for Lean Software*; Larus (2009), *Spending Moore's Dividend*

---

## 16. Open questions for the thesis

1. Given that §14.1 shows the IN column is narrow, should the project target **configuration space** or **data-access patterns** instead of, or alongside, function-level code mutation? Both have larger documented impact and lower correctness risk.
2. Can causal profiling (Coz) be adapted as the target-selection mechanism, replacing self-time ranking? Coz targets native code — what is the equivalent for a Python or JVM target?
3. What is the minimum detectable effect size for an auto-generated harness, and can it be certified before the search begins rather than discovered afterward?
4. Does rotating the benchmark input set between generations measurably suppress specification gaming compared to a fixed input set? This is a cheap, self-contained experiment and would be a publishable result on its own.
5. Does an optimizer under sustained selection pressure discover exploit categories absent from the single-shot agent taxonomies (EvilGenie's, RHB's six)? Also cheap to answer, and novel.
