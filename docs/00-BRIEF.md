# 00 — PROJECT BRIEF

**Read this first. It is the entry point for the whole document set.**

---

## 1. What we are building

An agentic system that finds performance problems in software **by running experiments**, not by reading code — then proposes fixes, verifies them against tests it wrote itself, and subjects them to an adversarial agent before anything reaches a human.

**One-line claim:**

> We automate the selection and sequencing of fourteen established performance-analysis methods, using an agent to decide which experiment to run next based on what the previous one revealed.

**Why an agent is required.** The methods are well-established and mechanizable. *Choosing which one applies to a given program*, sequencing them, and interpreting the results is documented in the fault-localization literature as requiring expert knowledge of the specific program. That selection problem is the agent's job, and the field named it as the bottleneck decades before LLMs existed.

**What we are not claiming.** We do not understand codebases. We do not make software fast in general. We do not replace performance engineering. Trustworthiness is exactly what the evaluation numbers say it is and no more.

---

## 2. Authority map — which file wins

The document set was written incrementally and earlier files contain superseded claims. **When files disagree, this order decides.**

| Topic | Authoritative file |
|---|---|
| Primitives, what we can detect | `01-primitives.md` |
| Layer contracts, artifact schemas | `02-architecture.md` |
| Agent configs, tools, prompts, LangGraph | `03-agents.md` |
| Cost engineering | `04-cost.md` |
| Problem space, citations | `05-research.md` |
| Validation history, honest limits | `06-validation.md` |
| Customer-facing capability list | `07-use-cases.md` |
| Design flaws and their fixes | `08-audit.md` |
| **What to build, in what order** | **`10-BACKLOG.md`** |

**Start at `10-BACKLOG.md` once you have read this brief.** It is the execution plan and it supersedes the build order in §5 below, which remains here as the summary view. Where `08-audit.md` corrects an earlier file, the backlog already incorporates the correction.

**Delete these — they are fully superseded and will confuse you:**

- `architecture-spec.md` — pre-agentic design, wrong model
- `agentic-architecture.md` — merged into `02` and `03`
- `capability-catalogue.md` — says six primitives; there are fourteen

**Known stale references:** `02-architecture.md` and `03-agents.md` were written against the six-primitive set. Their layer and agent designs remain correct; wherever they enumerate primitives, `01-primitives.md` governs.

---

## 3. Scope

### Covered

Web APIs and backends, monolithic applications (any era — age is irrelevant), libraries and packages, CLI tools, batch and data pipelines, test suites and CI, compilers and parsers. Legacy server-side software is usually *easier* than modern microservices.

### Partially covered

Microservice systems (diagnose across boundaries, fix only within accessible repos), LLM applications (needs its own instrument pack — see `01` §13), ML training, serverless, HPC.

### Not covered

Frontend and browser, mobile, desktop GUI, game engines (frame budget is the wrong metric for us), embedded firmware, mainframe and COBOL batch, kernel and OS.

**Coverage is determined by four questions, not by software age:** can we run it in isolation, drive it programmatically, instrument its runtime, and is average-case the right metric?

### Refused on principle

These are not gaps. They are categories where no verifier we can build makes the change safe.

| Refusal | Reason |
|---|---|
| **Concurrency and locking fixes** | Output equivalence cannot detect an introduced race. Diagnose and report only. |
| **Hard real-time systems** | Measurement-based analysis is provably insufficient for WCET. Worse, cache-style optimizations improve our metrics *while degrading worst-case timing*. Detect RTOS indicators and decline. |
| **Third-party dependency code** | Report the cause; do not patch other people's packages. |
| **Anything against production** | Test environments only, enforced by a database-URL pattern check that refuses to start otherwise. |

---

## 4. The metastability gate — read before implementing any fix path

Metastable failures arise from *optimizations for the common case* that remove system slack, creating feedback loops that sustain degradation after a trigger passes. Named triggers include retries and **caching**.

**Our tool produces exactly these optimizations.** A caching fix reduces steady-state queries, passes every check, and can move a system from stable to vulnerable — where the next traffic spike does not recover.

**Required gate.** Any patch that reduces slack or introduces a feedback loop — caching, retry logic, connection reuse, pool tightening, memoization — must:

1. Be flagged `slack-reducing` automatically, by pattern match on the diff
2. Pass a spike-and-recovery test (primitive 12) before it can be proposed
3. Always require human review, regardless of trust-ledger level

This gate is a safety requirement, not a quality one. Implement it before the Surgeon can emit its first patch.

---

## 5. Build plan

Each step has an acceptance criterion. Do not proceed until it is met.

### Phase 1 — Instruments (no AI at all)

**Step 1. The lab bench.**
Five operations: `execute`, `time`, `count`, `diff`, `stats`. Nothing else.
*Accept when:* you can run a Django endpoint, time it, and count its queries from the command line.

**Step 2. Ablation.**
Monkeypatch a target and measure the delta. This is the most-used primitive; build it before counters.
*Accept when:* stubbing a serializer method on a real project produces a clean, reproducible timing delta.

**Step 3. Scaling and screening.**
Seed at N, measure, fit growth. Screen all workloads, flag superlinear growth and high flat cost.
*Accept when:* screening a real repo produces a growth table and correctly reports "nothing found" on a healthy one.

**Step 4. Replay cache.**
Key on `(repo_sha, workload_id, experiment_spec, fixture_hash)`. Cache every measurement.
*Accept when:* a previously-run investigation replays in seconds with zero API calls. **This changes your iteration speed from ~5 cycles a day to ~50 — build it now, not later.**

### Phase 2 — The riskiest component

**Step 5. Explorer agent, alone.**
Give it an unfamiliar Django repo and one goal: return real data from one endpoint at controllable scale. No other agents.
*Accept when:* it grounds three repos it has never seen, and honestly reports failure on a fourth rather than claiming success on empty data.

**This step decides whether the project is viable.** If it fails here, everything downstream is moot.

### Phase 3 — Diagnosis

**Step 6. Diagnostician, one primitive.**
Must emit an evidence chain containing measurements. Schema-enforced.
*Accept when:* it finds a known N+1 and its output contains the growth table that proves it.

**Step 7. Second primitive — the thesis step.**
The agent must switch instruments when the first comes back flat.
*Accept when:* on a repo where query count is flat, it concludes "not the database," switches to ablation, and localizes the real cause. **This is the demo that justifies the whole architecture.**

### Phase 4 — Safety before repair

**Step 8. Execution mode separation.**
Diagnostic and candidate worktrees in separate containers. Diagnostic worktree destroyed on exit.
*Accept when:* a diff produced during ablation is structurally incapable of reaching the Surgeon.

**Step 9. The metastability gate** (§4 above).
*Accept when:* a patch introducing a cache is automatically flagged and blocked from auto-approval.

### Phase 5 — Repair and audit

**Step 10. Surgeon, test-first.**
Falsification test written and run against unpatched code before any patch exists.
*Accept when:* it refuses to write a patch when its own test passes on the original.

**Step 11. Adversary, isolated context.**
Separate message list, ideally a different model vendor.
*Accept when:* the ablation study shows it catches bad patches the Surgeon's own checks missed. **If the delta is small, cut it** — it would be theatre.

### Phase 6 — Durability and scale

**Step 12.** LangGraph checkpointing, interrupts, time travel. *Accept when:* killed mid-run, it resumes with full state.
**Step 13.** Playbooks and memory. *Accept when:* the tenth Django project takes materially fewer Explorer steps than the first.
**Step 14.** Trust ledger.
**Step 15.** Second framework adapter, then extract MCP. *Accept when:* it runs on SQLAlchemy without core changes.

**Steps 1–6 are a useful system. Step 7 is the thesis. Step 11 is the contribution.**

---

## 6. Evaluation

| Metric | Method |
|---|---|
| **Diagnostic agreement** | run diagnosis 10× on one repo, report agreement on the primary finding. This is the honest form of "reliable," and nobody publishes it for this domain |
| Capability | SWE-Perf instances, per-category, against expert patches |
| Learning curve | Explorer steps vs projects seen — should decline |
| Adversary value | ablation: with and without, count bad patches reaching a human |
| Cost | euros per confirmed finding |
| **Failure catalogue** | publish the repos where nothing was found, the caught cheats, the diagnoses that flipped between runs |

The failure catalogue is more credible than the success rate. Publish it.

---

## 7. When to stop researching

The primitive set will never be provably complete. That is a property of the domain, not a defect in the work. The useful question is when further research stops paying, and there is a concrete test.

**Stop when new findings stop changing structure.**

| Pass | Found | Changed the architecture? |
|---|---|---|
| 1 | off-CPU instrument, ddmin, fixture shape | yes |
| 2 | load axis, Coz's actual method, real-time refusal | yes |
| 3 | metastability | yes — invalidated a mandatory gate |
| 4 | input fuzzing, fault injection | **no — both are just new experiment types** |

By this test, research reached diminishing returns after pass three. Pass four's finds are additive: a new tool the Diagnostician can call, requiring no change to the graph, the state, the schemas, the gates, or any agent contract.

**The architecture makes this asymmetry deliberate.** Primitives are cheap to add — one tool, one entry in the instrument list. Structure is expensive to change: execution-mode separation, the checkpointed/persistent state split, the evidence-chain schema, the finding-audit placement, the instrument hook points. Spend design effort on the expensive side; add primitives as they turn up.

**Resume research only on evidence, not on doubt.** The trigger is a real repository producing a wrong or empty answer that the existing fourteen primitives cannot explain. That is ground truth. Literature search is a weak proxy for it and has been exhausted.

**For the thesis narrative**, this is also the stronger position: "we extended the set in response to observed failures" is a better methodology claim than "we read more papers," and it is falsifiable.

---

## 8. Viability checks — do these before writing much code

Three experiments, roughly a day each, any of which can invalidate the design:

1. **Grounding.** Take three arbitrary Django repos from GitHub. Can you stand them up with a populated database and hit one endpoint doing real work? If this is hard by hand, it is much harder for an agent.
2. **Ablation.** Monkeypatch a serializer method in a running Django app. Is the timing delta clean and reproducible?
3. **Reset.** Seed, run, roll back, ten cycles. Do row counts return identical every time? Everything downstream assumes this.

If all three work, the architecture is sound and the rest is engineering.

---

## 9. Non-negotiables

- **No finding without a measurement.** Enforced by schema, not by prompt.
- **Exclusions are recorded.** "Not the database, queries flat across 100× scale" is a result.
- **Null results are valid output.** "Screened 9 workloads, nothing found" ships as an answer.
- **Guard counters on every metric.** Queries down while rows explode is not an improvement.
- **The append-only experiment log.** Never reorder or re-summarize it — that invalidates prompt caching and multiplies cost.
- **Ablation runs can never produce a patch.** Structural, not procedural.
