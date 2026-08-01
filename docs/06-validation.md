# Research Validation

**Is the primitive set complete? Are we following established practice? Is the agent justified?**

A validation pass against the canonical performance-analysis literature. Companion to `capability-catalogue.md` and `performance-loss-taxonomy.md`.

**Outcome: five gaps found.** The primitive set goes from six to eight, one primitive gains a formal algorithm, and one instrument class was entirely missing.

---

## 1. What we validated against

| Framework | Origin | What it provides |
|---|---|---|
| Software Performance AntiPatterns | Smith & Williams, WOSP 2000, and successors | canonical catalogue of recurring performance defects |
| The USE Method | Gregg | checklist-driven resource coverage: utilization, saturation, errors |
| RED Method | Wilkie, 2015 | service-side coverage: rate, errors, duration |
| Delta Debugging | Zeller & Hildebrandt, IEEE TSE 2002 | formal algorithm for isolating failure-inducing circumstances |
| Roofline Model | Williams, Waterman & Patterson | bounds attainable performance against hardware limits |
| Causal Profiling | Curtsinger & Berger, SOSP 2015 | measures the effect of optimizing, not where time is spent |
| Fault Localization survey | Wong et al. | taxonomy of localization techniques and their limits |
| Off-CPU / TSA methodologies | Gregg | analysis of blocked rather than running time |

---

## 2. Gap 1 — Bound comparison (new primitive)

**What we missed.** All six original primitives compare **two runs**. None compares a run against a **model of what is achievable**.

The roofline model bounds attainable performance using two hardware limits — peak compute throughput and peak memory bandwidth — expressed as a function of arithmetic intensity, the ratio of operations to bytes moved. Kernels below the ridge point are memory-bound; above it, compute-bound. Kernels close to the roofline are already making good use of the hardware.

> Williams, S., Waterman, A., Patterson, D. "Roofline: An Insightful Visual Performance Model for Multicore Architectures." *CACM* 52(4), 2009.

**Why this matters more for an agent than for a human.** It answers a question none of the other primitives can: *is there any headroom here at all?* A workload at 76% of its attainable bound has essentially nothing left. One at 3% has thirty-fold available. Knowing this **before** launching an investigation is the single cheapest way to avoid forty wasted experiments.

**Generalizing beyond HPC.** The roofline formulation is FLOP-centric, but the idea is not. The general form is **minimum necessary work**:

| Domain | Bound | Compare against |
|---|---|---|
| Data processing | bytes that must be read | bytes actually read |
| API endpoint | distinct entities required | queries actually issued |
| Serialization | fields consumed downstream | fields actually serialized |
| Network | payload semantically required | bytes actually transferred |
| Any computation | instructions strictly required | instructions retired |

**Caveat to carry honestly:** the roofline model is intentionally optimistic and does not by default account for non-overlapping bottlenecks across multiple hierarchy levels. A bound is a ceiling, not a target.

---

## 3. Gap 2 — Off-CPU analysis (missing instrument class)

**What we missed.** Our five lab-bench operations measure elapsed time and count operations. Neither tells us **what a process was blocked on**.

Gregg lists Off-CPU Analysis and Thread State Analysis as distinct methodologies alongside the USE Method. Latency is frequently dominated by waiting — disk, network, lock acquisition, scheduler queueing, garbage collection pauses — and none of that appears in a CPU profile.

**Consequence for us:** an ablation that removes a component and sees a large drop tells us the component is expensive but not *why*. Off-CPU instrumentation distinguishes "it computed a lot" from "it waited a lot," and those have entirely different fixes.

**Implementation:** thread-state sampling, blocking-call instrumentation, or eBPF-based off-CPU stacks on Linux. Adds one instrument, not a new primitive.

---

## 4. Gap 3 — Longitudinal degradation (new primitive)

**What we missed.** Our scaling primitive varies input size at a fixed moment. Our temporal primitive varies commit. Neither varies **elapsed time within a single run**.

The antipattern catalogue names two defects that live exclusively on this axis:

- **The Ramp** — processing time increases as the system continues to run
- **Traffic Jam** — one problem produces a backlog that creates wide response-time variability persisting long after the original cause is gone

> Smith, C.U., Williams, L.G. "Software Performance AntiPatterns." *WOSP 2000*; and "New Software Performance AntiPatterns: More Ways to Shoot Yourself in the Foot," 2002.

Both are invisible to a benchmark that runs for thirty seconds. Also on this axis: memory leaks, cache pollution, connection-pool exhaustion, fragmentation, and index bloat.

**Method:** run the same workload repeatedly at fixed input size for an extended period; fit metrics against elapsed time rather than input size. A soak test, expressed as a primitive.

**Honest cost note:** this is the most expensive primitive to run and should be gated behind evidence that a long-running process is the deployment model.

---

## 5. Gap 4 — Workload shape, not only size

**What we missed.** Scaling varies *volume*. Real workloads have *skew*.

Gregg lists Workload Characterization as a methodology distinct from the USE Method, precisely because who generates the load and how it is distributed matters independently of how much of it there is.

**Concrete failure this causes us:** synthetic fixtures generate uniform data — every customer gets three orders. An N+1 that costs milliseconds at three orders and minutes at three thousand stays invisible at every scale we test, because we scaled the *number of customers* while holding the distribution flat.

**Fix:** the scaling primitive must vary along at least two axes — volume and distribution. Generate skewed fixtures (power-law, long-tail) as well as uniform ones. Our worked example already did this accidentally by varying both orders and promotions; it needs to be a rule rather than a lucky choice.

---

## 6. Gap 5 — Ablation has an algorithm, and we weren't using it

**What we missed.** Our agent selects ablation targets by judgment. Delta debugging already formalizes this.

`ddmin` takes a failing test case and simplifies it by successive testing until reaching a 1-minimal case, where removing any single remaining element would change the outcome. The stronger `dd` variant isolates the failure-inducing **difference** between a passing and a failing case rather than merely minimizing the failing one.

The Mozilla case study is the benchmark result: 896 lines of HTML reduced to the single causative line, and 95 user actions reduced to 3, in 139 automated test runs — about 35 minutes on hardware of the era. Crucially, it did this **without any understanding of the syntax or semantics of the input**.

> Zeller, A., Hildebrandt, R. "Simplifying and Isolating Failure-Inducing Input." *IEEE TSE* 28(2), 2002. DOI: 10.1109/32.988498

**Adaptation for performance.** Replace the boolean oracle ("does it crash?") with a threshold oracle ("does cost exceed X?"). Then `ddmin` gives us binary search over ablation subsets instead of sequential guessing.

**Impact:** localizing among 40 candidate components goes from ~40 ablations to ~log₂(40) ≈ 6 in the good case. That is both a large efficiency gain and a formal foundation for the primitive we rely on most.

**The `dd` variant matters more than `ddmin`.** We usually have both a fast case (small input) and a slow case (large input). Isolating the difference between them is exactly `dd`'s purpose.

---

## 7. The revised primitive set

| # | Primitive | Varies | Status |
|---|---|---|---|
| 1 | Scaling | input volume **and shape** | extended (Gap 4) |
| 2 | Ablation | presence of a component | now algorithmic via `ddmin`/`dd` (Gap 5) |
| 3 | Substitution | implementation or configuration | unchanged |
| 4 | Isolation | surrounding context | unchanged |
| 5 | Observation | nothing — counts events | **extended with off-CPU** (Gap 2) |
| 6 | Temporal | code revision | unchanged |
| 7 | **Bound comparison** | nothing — compares to a model | **new** (Gap 1) |
| 8 | **Longitudinal** | elapsed time within one run | **new** (Gap 3) |

Note the structural distinction: primitives 1–4 and 6 construct a contrast between two runs. Primitive 5 observes one run. Primitive 7 compares a run to a model. Primitive 8 compares a run to itself, later. **Those are four genuinely different epistemic moves**, and having all four is what makes the set defensible as complete rather than merely long.

---

## 8. Coverage matrix — canonical antipatterns

The Smith & Williams catalogue and its successors. Mapping every named antipattern to the primitive that finds it:

| Antipattern | Description | Found by | Fixable |
|---|---|---|---|
| **Circuitous Treasure Hunt** | retrieve an object, use it to retrieve another, repeat | scaling + observation | **yes** |
| **Sisyphus Database Retrieval** | retrieve an entire list to display a subset, repeatedly | scaling + observation | **yes** |
| **Empty Semi-Truck** | many messages each carrying little data | observation on bytes per call | **yes** |
| **Excessive Dynamic Allocation** | objects created and destroyed at high rate | observation on allocation count | **yes** |
| **The Ramp** | processing time grows as the system runs | **longitudinal (Gap 3)** | sometimes |
| **One-Lane Bridge** | a serialization point where few processes proceed | isolation + off-CPU | **diagnose only** |
| **Traffic Jam** | a backlog producing persistent response variability | **longitudinal + isolation** | **diagnose only** |
| **Unbalanced Processing** | work distributed unevenly across resources | isolation | diagnose only |
| **God Class** | one class doing too much, driving excess coupling | ablation localizes; fix is architectural | **diagnose only** |

**Coverage: 9 of 9 detectable. 4 of 9 automatically fixable.** The five diagnose-only entries are the concurrency and architecture categories we refuse by policy, and that refusal is now visibly principled rather than arbitrary — it maps to a published taxonomy.

Two of these nine were undetectable before this validation pass, which is the clearest justification for having done it.

---

## 9. Coverage matrix — the USE Method

Gregg's formulation: *for every resource, check utilization, saturation, and errors.* He describes it as an aircraft-emergency-style checklist — simple, complete, fast — and reports that it solves roughly 80% of server issues with 5% of the effort.

Resources include not only CPU, memory, storage and network but also thread pools, file descriptors, and locks.

| Resource | Utilization | Saturation | Errors |
|---|---|---|---|
| CPU | observation (instructions, time) | off-CPU (runqueue wait) | — |
| Memory | observation (peak RSS) | longitudinal (growth, swap) | OOM events |
| Storage | observation (I/O count, bytes) | off-CPU (I/O wait) | I/O errors |
| Network | observation (calls, bytes) | off-CPU (connect wait) | timeouts, retries |
| Connection pool | observation (in use) | off-CPU (acquire wait) | pool exhaustion |
| Thread pool | observation | off-CPU (queue depth) | rejections |
| Locks | isolation | off-CPU (lock wait) | — |

**Before Gap 2, the entire saturation column was empty.** That is a striking result: the USE Method's central insight is that saturation is where bottlenecks announce themselves, and our instrument set could not see it at all. Adding off-CPU analysis completes the matrix.

RED (rate, errors, duration) is covered by the workload-level metrics in screening.

---

## 10. Confirmations — where we were already right

**Causal profiling validates the target-selection rule.** Curtsinger and Berger's central point is that profilers report where programs spend time, and optimizing that code may have no impact on performance. Their SQLite result — a 25% speedup from a function accounting for ~0.15% of runtime — is the evidence behind our rule against selecting targets by self-time. Their approach is itself perturbation-based, which is the same epistemic move as our ablation primitive.

**Delta debugging validates ablation as a principled method,** not an ad-hoc trick, and supplies the algorithm we were missing.

**The USE Method validates checklist-driven screening.** Our deterministic screening phase is structurally the same move: cheap, systematic, complete coverage before expensive analysis begins.

**"Change one thing at a time, then re-measure the same signals to confirm improvement and rule out placebo"** is standard practice in the systems-performance methodology literature, and it is exactly our experiment discipline.

---

## 11. Is the agentic approach justified? The literature says so directly

This is the strongest finding of the validation pass, and it should be quoted rather than argued.

**From the fault localization survey:** locating bugs is more an art form than an easily-automated mechanical process; techniques exist that narrow the search domain, but a particular method is not necessarily applicable to every program, and **choosing an effective debugging strategy normally requires expert knowledge regarding the program in question.**

> Wong, W.E. et al. "A Survey of Software Fault Localization." Technical Report UTDCS-45-09.

**From Gregg's methodology index:** different methodologies are suited to different classes of issue, and **you may try more than one before accomplishing your goal.**

Put together, the field's own position is:

| Layer | Status in the literature |
|---|---|
| The methods themselves | well-established, formalized, mechanizable |
| **Choosing which method to apply** | **requires expert knowledge of the specific program** |
| **Sequencing and iterating when one fails** | **explicitly expected, explicitly unautomated** |
| Interpreting results in context | requires judgment |

**That middle band is exactly what the Diagnostician does**, and the justification comes from the field rather than from us. The claim is not "agents are better at performance analysis." It is: *the methods were already automatable; method selection was the bottleneck, and the literature said so decades before LLMs existed.*

This also sets the honest scope of the contribution. We are not inventing performance analysis techniques. We are automating the selection and sequencing of established ones — which is the part that was documented as requiring expertise.

---

## 12. What remains genuinely uncovered

Honest accounting after the validation pass.

| Not covered | Why | Could it be added? |
|---|---|---|
| Analytical queueing models (Little's Law, Universal Scalability Law) | predicts behaviour under load we do not generate | yes — needs load generation, meaningful scope increase |
| Hardware counter analysis (cache misses, branch mispredictions, false sharing) | needs PMU access, often unavailable in containers | partially, on bare metal |
| Multi-level bottleneck interaction | roofline is single-bottleneck by construction; ECM models address this | out of scope |
| Energy and power | different instrument class entirely | possible, separate work |
| Frontend and browser performance | different runtime, different instruments | separate project |
| Production-only phenomena | we run in test environments by design | no — and this is deliberate |

The first row is the most defensible omission to flag in a thesis: analytical modelling predicts what happens at loads you have not run, which is genuinely valuable and genuinely outside an experiment-driven system.

---

## 13. Consequences for the build

Changes required by this validation pass:

| Change | Where | Priority |
|---|---|---|
| Implement `ddmin`/`dd` for ablation target search | Diagnostician tooling | **high** — large efficiency gain, low effort |
| Add off-CPU instrumentation | lab bench | **high** — an entire USE column is blank without it |
| Vary fixture shape as well as size | Explorer fixture synthesis | **high** — uniform data hides real defects |
| Add bound comparison as a pre-investigation check | Screening | **medium** — prevents wasted investigations |
| Add longitudinal primitive | Diagnostician | **low** — expensive; gate behind deployment model |

The first three are corrections to things that would otherwise have been quietly wrong. The fourth is a cost optimization. The fifth is a scope extension.

---

## 14. Reading list for the thesis background chapter

**Foundational methods:**

1. Zeller & Hildebrandt (2002), *Simplifying and Isolating Failure-Inducing Input*, IEEE TSE — the algorithm behind ablation
2. Curtsinger & Berger (2015), *Coz: Finding Code that Counts with Causal Profiling*, SOSP — why profilers mislead
3. Williams, Waterman & Patterson (2009), *Roofline*, CACM — bound analysis
4. Smith & Williams (2000), *Software Performance AntiPatterns*, WOSP — the defect catalogue
5. Gregg, *Systems Performance* and the USE Method — resource coverage methodology

**Measurement validity** (from `performance-loss-taxonomy.md`):

6. Mytkowicz et al. (2009), ASPLOS — measurement bias
7. Laaber et al. (2019), EMSE — cloud variability
8. Barrett et al. (2017), OOPSLA — steady state is not reached

**Justification for automation:**

9. Wong et al., *A Survey of Software Fault Localization* — method selection requires expertise
10. Leitner & Bezemer (2017), ICPE — performance testing is under-adopted because it is high-friction

**Evaluation:**

11. SWE-Perf (arXiv:2507.12415)
12. *Are Performance-Optimization Benchmarks Reliably Measuring Coding Agents?* (arXiv:2607.01211)

---

## 15. The claim after validation

**Before:** "we use six experiment primitives to find performance problems."

**After:** "we automate the selection and sequencing of eight established performance-analysis methods — ablation grounded in delta debugging, bound comparison grounded in the roofline model, resource coverage grounded in the USE Method, and target selection corrected for the misattribution that causal profiling documents — across a defect space validated against the canonical antipattern catalogue, where 9 of 9 named antipatterns are detectable and 4 are automatically fixable."

That is a materially stronger and more defensible claim, and every clause in it has a citation behind it.
-e 

---

# PASS TWO AND THREE

# Research Validation, Second Pass

**Brutally honest: is the primitive set complete, and what software can we actually handle?**

Supersedes the completeness claims in `research-validation.md`. Companion to `capability-catalogue.md`.

---

## 0. The headline finding

**Pass one:** six primitives → found five gaps → eight primitives.
**Pass two:** eight primitives → found three more gaps → eleven primitives.

The discovery rate is not declining. **That is the most important result in this document.** Two independent validation passes each found material omissions, which is evidence that a third would too.

Any claim that the primitive set is complete is unfalsifiable and should not be made. What can honestly be claimed: *complete with respect to the frameworks validated against, with a documented method for discovering more.*

I also need to retract a claim from pass one. The "9 of 9 antipatterns covered" figure was measured against a catalogue discovered during the same search that produced it. Choosing the yardstick after measuring is weak evidence and I presented it as strong. Those nine are covered; nine is not established as the population.

---

## 1. Gap 6 — Load variation (the largest omission)

**What we missed.** Every existing primitive varies data *size*. None varies *concurrency*.

These are orthogonal failure modes. A system can be flawless at 10⁶ rows with one user and collapse at 50 concurrent users with 10 rows. Everything production engineers actually lose sleep over lives on the concurrency axis: connection-pool exhaustion, lock convoys, queueing delay, thread-pool saturation, tail-latency amplification.

**The formal model.** Gunther's Universal Scalability Law expresses throughput X(N) against load N with three coefficients:

- **Concurrency (γ)** — ideal linear scaling
- **Contention (α)** — waiting or queueing for shared resources
- **Coherency (β)** — delay for data to become consistent via point-to-point exchange

The contention term creates a horizontal asymptote — an absolute throughput ceiling regardless of added load. The coherency term makes throughput *decrease* beyond a peak. From a handful of measurements the model yields **Nmax**, the maximum concurrency the system can usefully handle.

Critically, USL reduces to Amdahl's Law when β = 0, so it generalizes the law we have been implicitly reasoning with.

> Gunther, N.J. "How to Quantify Scalability: The Universal Scalability Law." Originally presented CMG 1993 (as the super-serial model). See also Schwartz, B. *Practical Scalability Analysis with the Universal Scalability Law*.

**Method as a primitive.** Run the workload at increasing concurrency at fixed data size. Fit throughput and latency against N. Report α, β, and Nmax.

**Why this is especially valuable for an agent:** the fitted coefficients are *diagnostic*, not merely descriptive. High α points at a shared resource — the One-Lane Bridge antipattern. High β points at coordination or cache-coherence cost. The model tells the agent which hypothesis class to pursue next.

**Related:** Little's Law (L = λW) relates throughput, latency and concurrency, so measuring any two yields the third. Useful for cross-checking that measurements are self-consistent.

**Honest limitation:** this primitive can only diagnose. Contention fixes remain in our refusal list, because output equivalence cannot verify the absence of an introduced race.

---

## 2. Gap 7 — Proportional perturbation (we cite Coz but don't implement it)

**What we missed, and it is embarrassing.** Coz works by **virtually speeding up** a line: inserting pauses in all concurrently-running code whenever that line executes, thereby measuring the causal effect of optimizing it.

We adopted Coz's *conclusion* — do not select targets by profiler self-time — while implementing ablation, which is a different operation.

| | Ablation | Proportional perturbation |
|---|---|---|
| Operation | remove the component entirely | slow everything else by a fixed fraction |
| Answers | what does this **cost**? | what would optimizing it **gain**? |
| Correctness | broken by construction | preserved |
| Concurrent systems | misleading — removing a bottleneck promotes the next | accurate — models a partial speedup |

The divergence matters most exactly where performance work is hardest. Ablating a component in a concurrent pipeline can show a 40% drop while a realistic 2× speedup of that component yields nothing, because a different stage becomes the critical path. Coz's own worked example shows two functions with similar profile weight where optimizing one yields at most 4.5% and the other yields zero.

> Curtsinger, C., Berger, E.D. "Coz: Finding Code that Counts with Causal Profiling." *SOSP 2015* (Best Paper); *CACM* 61(6), 2018.

**Practical form for us:** rather than stubbing a component, run it with an artificial delay injected into *everything else* proportionally — or, more simply for single-threaded code, inject a known slowdown into the component and extrapolate the sensitivity curve. Either gives marginal benefit rather than total cost.

**Rule this establishes:** ablation for *localization*, proportional perturbation for *prioritization*. We were using one tool for both jobs.

---

## 3. Gap 8 — Platform variation

**What we missed.** Substitution varies the *artifact*. Nothing varies the *environment*.

Real and common reports we currently cannot investigate:

- slow only on the new ARM nodes
- slow only after the runtime upgrade
- slow only in the container, fine on the host
- slow only on this kernel version
- slow only with this allocator

**Method:** hold code and input constant, vary the execution environment. Runtime version, CPU architecture, container versus host, allocator, OS version, filesystem.

**Note on validity:** this is precisely the axis Mytkowicz et al. showed produces measurement bias — environment variable size and link order alone can flip a conclusion. So this primitive must be used with the setup randomization they recommend, or it produces exactly the false results it is meant to detect.

---

## 4. The revised set — eleven primitives, organized by what varies

| # | Primitive | What varies between the two things compared |
|---|---|---|
| 1 | Scaling | input volume **and shape** |
| 2 | **Load** | **concurrency** |
| 3 | Longitudinal | elapsed time within one run |
| 4 | Temporal | code revision |
| 5 | Ablation | component presence |
| 6 | **Proportional perturbation** | **component speed** |
| 7 | Substitution | implementation or configuration |
| 8 | **Platform** | **execution environment** |
| 9 | Isolation | surrounding context |
| 10 | Observation | nothing — single run, counted |
| 11 | Bound comparison | nothing — run against a model |

**Structure:** nine construct a contrast between two executions; one observes a single execution; one compares an execution to a model. Those are three distinct epistemic moves, and having all three is the strongest structural argument that the set is broadly complete — though not proof.

**Search strategy for pass three**, when you do it: look for techniques that vary something not in the middle column. Candidate unexplored axes include varying the *data distribution independently of volume*, varying *failure conditions* (chaos engineering), and varying *the compiler or build configuration*.

---

## 5. Software coverage — age is the wrong axis

You asked whether we cover old, traditional, and foundational software as well as modern. The framing turns out to be misleading.

A 1998 Perl CGI application that runs under Docker is **fully covered**. A 2026 iOS application is **not**. Four questions determine coverage, and none is age:

1. **Can we run it in isolation?** A test environment, not production.
2. **Can we drive it programmatically?** An HTTP endpoint is easy; a GUI is hard.
3. **Can we instrument its runtime?** Is there a hook point?
4. **Is average-case the right metric?** For games and real-time systems, it is not.

### Coverage table

| Software type | Run? | Drive? | Instrument? | Metric OK? | Verdict |
|---|---|---|---|---|---|
| Web API / backend service | ✓ | ✓ | ✓ | ✓ | **full** |
| Monolithic application | ✓ | ✓ | ✓ | ✓ | **full** — easier than microservices |
| Library or package | ✓ | ✓ | ✓ | ✓ | **full** |
| CLI tool | ✓ | ✓ | ✓ | ✓ | **full** |
| Batch / data pipeline | ✓ | ✓ | ✓ | ✓ | **full** |
| Test suite / CI | ✓ | ✓ | ✓ | ✓ | **full** |
| Compiler, parser, interpreter | ✓ | ✓ | ✓ | ✓ | **full** |
| Legacy monolith (any era) | ✓ if containerizable | ✓ | ✓ | ✓ | **full when runnable** |
| Microservice system | partial | ✓ | ✓ | ✓ | diagnose across, fix within |
| LLM application | ✓ | ✓ | ✓ | different metrics | **partial — see §7** |
| ML training | ✓ | ✓ | ✓ | ✓ | partial — needs GPU |
| Serverless function | ✓ locally | ✓ | ✓ | ✓ | partial — prod behaviour differs |
| HPC / scientific | hard | ✓ | ✓ | ✓ | partial — scale, MPI |
| Database engine itself | ✓ | ✓ | ✓ | ✓ | partial — specialized field |
| Frontend / browser | ✓ | needs automation | different | different | **poor — separate project** |
| Mobile application | needs device | needs automation | platform-specific | battery, thermal | **poor** |
| Desktop GUI | ✓ | needs UI automation | ✓ | ✓ | **poor — driving is the blocker** |
| Game engine | ✓ | ✓ | ✓ | **frame budget, p99** | **poor — wrong metric** |
| Embedded firmware | ✗ host | hardware-in-loop | limited | ✓ | **not covered** |
| **Hard real-time** | ✓ | ✓ | ✓ | **✗ fundamentally** | **refuse — see §6** |
| Mainframe / COBOL batch | usually ✗ | ✗ | ✗ | ✓ | **not covered** |
| Kernel / OS | ✗ | ✗ | different | ✓ | **not covered** |
| Distributed consensus | ✓ | ✓ | ✓ | ✓ | diagnose only — oracle insufficient |

**The pattern:** coverage tracks *runnability and drivability*, not modernity. Old server-side software is usually well covered because it tends to be batch or request/response — the easiest shapes to drive. Modern client-side software is often poorly covered because driving a UI is hard.

---

## 6. Hard real-time — where we could cause active harm

This deserves its own section because it is the only category where running our system could make things **worse** while every metric reports success.

**Why measurement is structurally insufficient.** The WCET literature states plainly that in hard real-time systems, where deadline violations cannot be tolerated, it is often insufficient to rely on measuring execution times of tasks with various inputs — symbolic execution and static WCET analysis are required instead, because measurement exercises only a subset of paths and the worst case may never be observed. In practice, measurement-based estimates are corrected by adding a safety margin — for example 20% above the longest observed time — precisely because they are known to be optimistic.

Our entire approach is measurement-based. We cannot produce the guarantee these systems need.

**Why it is worse than merely inapplicable.** Hardware features such as caches and branch prediction raise average speed *at the cost of timing predictability*, and that unpredictability must be pessimistically bounded. Timing-predictable processors deliberately execute instructions in a fixed number of cycles regardless of data, so that WCET analysis can focus on control flow.

So an optimization that introduces caching or a data-dependent fast path would:

- improve every metric we measure ✓
- pass every correctness check we run ✓
- **increase worst-case execution time** ✗
- potentially break a certified deadline guarantee ✗

**Conclusion:** hard real-time joins concurrency in the refusal list, and for the same structural reason — no verifier we can build makes the change safe. The system must detect real-time indicators (RTOS imports, deadline annotations, safety-certification markers) and decline.

---

## 7. LLM applications — covered, but the instruments are wrong

You specifically asked about modern AI applications. The primitives apply; **the metric model does not**, and using our current one would produce nonsense.

### Why a single latency number is meaningless here

LLM inference has two phases with opposite characteristics. **Prefill** processes the entire prompt in one parallel pass and is compute-bound; it determines Time To First Token. **Decode** generates one token at a time and is memory-bound; it determines Time Per Output Token.

The consequences for measurement are severe:

- **Batching boosts decode throughput enormously but has almost no effect on prefill.** An optimization can improve one phase and degrade the other.
- Decode has such low arithmetic intensity that the cost of a linear operation for **one decode token is roughly the same as for 128 prefill tokens**.
- Standard metrics are TTFT, TPOT, Time Between Tokens, and Time To Last Token — not "latency."

> Agrawal, A. et al. "Taming Throughput-Latency Tradeoff in LLM Inference with Sarathi-Serve," arXiv:2403.02310. See also "On Evaluating Performance of LLM Inference Serving Systems," arXiv:2507.09019.

### What this requires from us

| Change | Detail |
|---|---|
| **Two independent scaling axes** | input tokens and output tokens vary independently and behave oppositely — our single-axis scaling would draw wrong conclusions |
| **Phase-decomposed timing** | TTFT and TPOT measured separately; a mean of the two is meaningless |
| **Token counters** | input, output, cached — cost is per token, so tokens are the natural counted resource |
| **Cost as a first-class metric** | uniquely in this domain, money is directly measurable per request, not inferred |
| **Cache-hit rate** | prompt caching changes cost by an order of magnitude |
| **Bound comparison applies natively** | arithmetic intensity and the roofline are the standard analysis frame for inference |

### What our primitives find in LLM applications

- **Ablation on a RAG pipeline** — stub retrieval, stub reranking, stub the model call. Localizes cost across the chain exactly as elsewhere. Works well.
- **Observation on token counts** — finds prompt bloat, redundant context, unnecessary re-sending of stable prefixes.
- **Scaling on context length** — finds quadratic attention cost and context-window mismanagement.
- **Substitution on model tier** — the highest-value optimization in this domain, and directly measurable.
- **Load variation** — finds batching and queueing behaviour, which is where serving systems actually fail.
- **Observation on cache hits** — finds prefix instability destroying prompt caching.

**Assessment:** LLM applications are a *good* fit for this system — arguably better than average, because cost is directly measurable and token counts are perfectly deterministic. But it needs its own instrument pack, and shipping the generic one would produce confidently wrong answers.

---

## 8. Legacy and mainframe — honest position

**Legacy server-side software is usually well covered.** A twenty-year-old Java monolith or Perl application is often *easier* than a modern microservice mesh: single process, single database, request/response or batch, no distributed tracing needed. If it containerizes, it is fully in scope.

**Mainframe and COBOL batch are not covered**, and the blockers are not technical subtleties:

| Blocker | Consequence |
|---|---|
| Cannot stand up a test environment | primitive 0 fails — nothing else runs |
| No programmatic drive path | JCL batch submission, not an API |
| No instrumentation hook | proprietary runtime, no profiler access |
| Batch windows | a run takes hours, not seconds |
| Data cannot leave the environment | no realistic fixtures available |

This is the same wall the black-box legacy-modernization research hits, and it is why that work probes the system as an oracle rather than instrumenting it. If you ever want this category, that is the technique — but it is a different project.

---

## 9. Revised claims

**Withdraw:**
- "Six primitives" — it was never six
- "9 of 9 antipatterns" as evidence of completeness — the yardstick was chosen after the fact
- Any implication that the primitive set is closed

**Assert instead:**
- Eleven primitives, validated against eight established frameworks, organized by what each varies
- Two validation passes, each finding material gaps — completeness is not claimed
- Coverage determined by runnability, drivability, instrumentability, and metric fit — not by software age
- Two categories refused on principle: concurrency fixes and hard real-time, both because no verifier we can build makes them safe

**The strongest honest sentence available:**

> *"We automate the selection and sequencing of eleven established performance-analysis methods. The set was assembled by validating against the canonical frameworks in the field and revised twice, each revision finding material gaps — so it should be treated as current best coverage rather than as complete."*

That is weaker than a completeness claim and considerably more credible than one.

---

## 10. What changes in the build

| Change | Priority | Effort |
|---|---|---|
| Add load variation with USL fitting | **high** — largest gap, and where production failures live | medium |
| Add proportional perturbation for prioritization | **high** — we cite Coz without using it | medium |
| Detect real-time indicators and refuse | **high** — safety, not features | low |
| LLM instrument pack (TTFT/TPOT, dual scaling axes, tokens) | medium — large and growing market | medium |
| Platform variation | low — needs multiple environments | high |

The third row is the only one that is a safety requirement rather than a capability. It should ship before the system is ever pointed at an unfamiliar repository, because the failure mode is silent.

---

## 11. Additional citations for the background chapter

Adding to the pass-one list:

13. Gunther, N.J. *Guerrilla Capacity Planning* / "How to Quantify Scalability: The USL" — load-axis analysis
14. Schwartz, B. *Practical Scalability Analysis with the Universal Scalability Law* — applied treatment
15. WCET literature — Wilhelm et al., "The worst-case execution-time problem," ACM TECS 2008 — why measurement is insufficient for real-time
16. Agrawal et al. (2024), *Sarathi-Serve*, arXiv:2403.02310 — prefill/decode asymmetry
17. "On Evaluating Performance of LLM Inference Serving Systems," arXiv:2507.09019 — the metric set for LLM applications
