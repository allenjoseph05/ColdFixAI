# 08 — DESIGN AUDIT

**Flaws found in the primitives, the agents, the state, and the flow — with fixes**

Read alongside `01-primitives.md` and `03-agents.md`. Where this file contradicts them, this file wins.

---

## 1. Severity summary

| # | Flaw | Severity | Fix cost |
|---|---|---|---|
| F1 | Metastability gate is unexecutable in our sandbox | **critical** | redesign gate |
| F2 | Nobody audits the diagnosis, only the patch | **critical** | new node |
| F3 | Exclusions recorded as unconditional facts | **high** | schema change |
| F4 | Playbook writes are unvalidated — errors compound across runs | **high** | validation gate |
| F5 | Rewind discards the failure knowledge that motivated it | **high** | split state |
| F6 | Explorer's "does real work" test is self-judged | **high** | objective threshold |
| F7 | Proportional perturbation degenerates on single-threaded code | medium | scope it |
| F8 | Bound comparison often circular | medium | restrict to computable cases |
| F9 | No primitive constructs adversarial inputs | medium | accept gap or add fuzzing |
| F10 | Guard counters are a denylist | medium | add global resource envelope |
| F11 | Ablation stub return value is under-specified | medium | specify |
| F12 | Retry "must differ" is self-judged | low | structural check |
| F13 | Checkpoint size grows with experiment log | low | store by reference |
| F14 | Post-patch screening staleness undefined | low | decide policy |
| F15 | Trust ledger transfers across projects unsafely | medium | scope per project shape |
| F16 | Human sees the work only after all cost is spent | medium | early checkpoint |

---

## 2. Primitive flaws

### F1 — The metastability gate cannot run

`00-BRIEF.md` §4 makes a spike-and-recovery test mandatory for slack-reducing patches. **That test is not executable in our environment.**

Metastable failure requires a sustaining feedback loop: many clients, retry logic, load balancing, queues feeding each other. In a single container with one synthetic driver, the loop does not exist. We can generate load. We cannot generate metastability.

**Corrected gate:**

1. Statically classify the diff. Patterns: added cache or memoization, retry logic, connection reuse, pool size reduction, timeout reduction, added buffering.
2. If matched, label `slack-reducing` and **block auto-approval permanently** — no trust level can clear it.
3. Emit a specific staging warning: *"This patch removes headroom at X. Before production, verify recovery after a load spike to 2× capacity."*
4. Do not claim we tested it.

Primitive 3 is downgraded from *verification we perform* to *risk class we detect and hand off*. That is honest and still valuable — nobody else flags this at all.

### F7 — Proportional perturbation is concurrency-specific

Coz's virtual speedup works by pausing *concurrently running* threads while the target executes. In single-threaded code there is nothing to pause; slowing everything else simply slows everything, and the primitive collapses back into ablation.

**Fix:** gate primitive 7 behind a concurrency check. In async or multi-threaded code it is the correct tool for prioritization. In a synchronous request handler, use ablation and say so. Do not present it as generally applicable.

### F8 — Bound comparison is often circular

"How many queries *must* this endpoint issue?" is a question about intent. If the agent could compute the minimum necessary work, it would already know the fix.

**Computable bounds** (keep): bytes that must be read for a data transform, instructions retired versus a hand-written lower bound, rows that must be returned given the response schema.

**Not computable** (drop): semantic minimums for arbitrary business logic.

**Consequence:** screening reduces to scaling plus flat-cost detection in the general case. Bound comparison applies opportunistically, not as a universal pre-check. My earlier claim that it prevents wasted investigations was overstated.

### F9 — Nothing searches input space

Every primitive varies size, shape, concurrency, environment, or component presence. **None searches for pathological inputs.**

Uncoverable as a result: regex catastrophic backtracking (ReDoS), hash-collision attacks, worst-case sort or hash-table inputs, deeply nested structure parsing, algorithmic complexity attacks generally.

These are real, common, and sometimes security-relevant. Performance fuzzing (SlowFuzz, PerfFuzz) is the established technique — mutate inputs, select for slowness.

**Decision required:** either add a fuzzing primitive (meaningful scope increase, needs a corpus and a mutation engine) or state the gap explicitly in `07-use-cases.md` §10. My recommendation is to state the gap for v1 and note it as the highest-value extension.

### F10 — Guard counters are a denylist

We guard `db.query` with `db.rows_returned`. But a fix can trade queries for memory, CPU, disk, file descriptors, or startup time — any resource we did not think to pair.

Denylists fail by omission, and an optimizer under selection pressure is exactly the process that finds omissions.

**Fix:** add a **global resource envelope** measured on every candidate — peak RSS, total CPU time, wall time, bytes written, file descriptors, process count. Any metric outside a tolerance band relative to baseline triggers a flag, regardless of whether we predicted that trade. Cheap to measure, catches the unanticipated case.

### F11 — Ablation stub returns are under-specified

Stubbing requires returning something type-correct or the workload crashes and the measurement is void. But **the choice of return value changes the measurement**:

| Stub returns | Measures |
|---|---|
| Empty collection | cost of the component *plus* all downstream work that consumed its output |
| A cached real value | cost of the component alone |
| A minimal valid value | somewhere between, unpredictably |

These give materially different numbers and the agent may not notice which it got.

**Fix:** default to **record-and-replay** — capture a real return value during a baseline run, replay it during ablation. This isolates the component's own cost. Where replay is impossible (streams, stateful objects), fall back to a minimal value and **record which strategy was used in the experiment log**, because the interpretation differs.

---

## 3. Agent-by-agent audit

### 3.1 Explorer

**Receives:** repo path, framework fingerprint, matching playbook entries, a sliding window of its last 20 action/observation pairs.

**Produces:** a workload object.

**Flaw F6 — the success criterion is self-judged.** We require `evidence_of_work`, but the agent decides what counts, and it is incentivized to say yes because saying yes completes its task. An endpoint returning three rows in three queries might be "working" or might be a stub route.

**Fix — objective threshold, not agent judgment:**

```
work_verified = (
    queries_at_n100 > queries_at_n10          # responds to data volume
    and response_bytes_at_n100 > 2 × at_n10   # returns more data
    and wall_time_at_n100 > 1.5 × at_n10      # does more work
)
```

Computed by the harness. The agent cannot override it. If false, the workload is rejected regardless of what the agent claims.

**Flaw F4 — playbook poisoning.** The Explorer writes playbook entries that all future runs trust. A wrong entry — *"DRF always uses TokenAuthentication"* — propagates silently and compounds. Nothing validates a write.

**Fix:**
- Playbook entries are **provisional on write** and carry a use counter with success and failure tallies.
- An entry is promoted to trusted only after N successful uses across **different projects**.
- An entry that fails twice is demoted and quarantined.
- Entries are scoped by fingerprint, never global.

**Capability gap:** the Explorer has no tool to inspect running processes or read container logs during a failed startup. A database that starts but rejects connections looks identical to one that never started. Add `logs(service)` and `ps()`.

**Verdict:** the riskiest component, and the two flaws above are both silent-failure modes. Fix before building anything downstream.

### 3.2 Diagnostician

**Receives:** workload, screening result, the full append-only experiment log, source of the region under suspicion, instrument list.

**Produces:** an evidence chain.

**Flaw F3 — exclusions are conditionally true but recorded as facts.** "Not the database — queries flat at 7, 7, 7" holds *at the scales tested, with the fixtures used, on this platform*. If the fixtures were uniform and the real defect is skew-dependent, the exclusion is false — and it sits in the prompt as established fact, permanently blocking the correct hypothesis.

**Fix:** every exclusion carries its preconditions and may be reopened.

```
exclusion:
  hypothesis:     "database is the bottleneck"
  measurement:    queries 7, 7, 7 at n = 10, 100, 1000
  conditions:     fixture_shape=uniform, platform=x86_64,
                  concurrency=1, scales=[10,100,1000]
  invalidated_if: fixture shape changes, concurrency > 1
```

When a later experiment changes a condition, the Diagnostician is shown which exclusions are now stale and may re-test them.

**Flaw — schema enforcement validates presence, not inference.** "No finding without a measurement" prevents fabrication. It does not prevent a correct measurement supporting a wrong conclusion. The agent can measure faithfully and reason badly.

**Fix:** this is exactly what F2's finding-audit exists to catch. Schema validation and adversarial review address different failure modes; we had only the first.

**Capability gap:** the Diagnostician cannot request new fixtures. If it suspects a skew-dependent defect, it has no way to ask for skewed data. Add `reseed(shape_spec)` calling back into the Explorer's fixture machinery.

**Context risk:** at 40 experiments the log is large. Per `04-cost.md` §5, summaries live in context and details are fetched on demand — but the agent must know detail is available. State this explicitly in the prompt, or it reasons from summaries alone and misses the stack traces it needed.

### 3.3 Surgeon

**Receives:** evidence chain, implicated files, prior attempts with failure reasons.

**Produces:** falsification test, then patch.

**Flaw — the test is written by the agent that then writes the patch.** The Surgeon can write a weak test to make its own life easier. We noted the Adversary audits the test, but only *after* the patch exists — by then the weak test has already shaped the patch.

**Fix:** the falsification test is submitted and audited **before** the patch is written. That is a second cheap Adversary call, and it costs far less than a wasted repair cycle.

**Flaw F12 — "must differ in approach" is self-judged.** The agent writes its own `approach` string and can rename the same idea.

**Fix:** structural check — compare the diffs. If attempt 2 touches the same lines with a similar edit shape as attempt 1, reject it before running any gate.

**Capability gap:** the Surgeon cannot run the primitives. It receives the evidence chain but cannot test a hypothesis about its own fix — for example, "would this also help at higher concurrency?" Give it read-only access to `scale` and `run_workload`.

### 3.4 Adversary

**Receives:** original code, patched code, evidence chain, falsification test.

**Flaw — context isolation is weaker than claimed.** The evidence chain and the falsification test both encode the Diagnostician's and Surgeon's framing of what matters. The Adversary inherits that frame even without seeing their reasoning.

**Honest position:** isolation is *partial*. It removes the explicit rationalization, which is the documented risk — 72% of reward-hacking episodes carry explicit justifying reasoning. It does not remove framing bias. Say this rather than claiming clean separation.

**Partial mitigation:** on its finding-audit pass (F2), give the Adversary the raw experiment log rather than the assembled evidence chain, so it sees the measurements before someone else's interpretation of them.

**Flaw F2 — it only audits patches.** Covered in §4 below.

---

## 4. F2 — The missing finding audit

The Adversary attacks equivalence, cheats, trades, and scope. **If the diagnosis is wrong, all of those pass.** A correct fix to a non-problem is equivalent, is not a cheat, trades nothing, and breaks no callers. It ships.

The entire adversarial apparatus sits downstream of an unaudited claim.

**Fix — the Adversary runs twice.** Same agent role, two invocations, new graph node between `investigate` and `repair`.

**Finding audit attacks:**

| Attack | Question |
|---|---|
| Exclusion validity | were the ruled-out hypotheses ruled out under adequate conditions? |
| Fixture adequacy | could the fixture shape have hidden the real cause? |
| Scale adequacy | were the tested scales large enough to separate linear from superlinear? |
| Alternative explanation | is there a different mechanism consistent with the same measurements? |
| Reproducibility | re-run one key experiment. Does it give the same number? |
| Representativeness | is this workload something users actually exercise? |

**Verdicts:** `sound` proceeds to repair. `unsound` returns to investigate with the objection. `unrepresentative` skips to the next finding.

**Cost:** ~10 calls. It kills bad branches before the repair phase, which costs ~50. Net saving, plus it closes the hole.

---

## 5. LangGraph and state flaws

### F5 — Rewind discards the knowledge that motivated it

Time travel restores state at checkpoint T. But the reason for rewinding is a failure discovered at T+n — and that failure record lives in the state being discarded. We rewind and the agent repeats the same attempt.

**This inverts the intent.** We want to rewind the *code* and keep the *learning*.

**Fix:** split the state.

```
CheckpointedState:     project, workloads, chains, current, budget
PersistentStore:       failure_memory, playbooks, ledger, replay_cache
```

The persistent store is a separate database, written append-only, never rolled back by a checkpoint restore. On rewind, the agent resumes with the earlier code state and the *later* knowledge.

### F13 — Checkpoint size

`experiments` is append-only and lives in checkpointed state. Forty experiments × full measurement output, checkpointed after every node, is megabytes of duplicated writes.

**Fix:** store experiment results in the replay cache keyed by hash; the state holds hashes and one-line summaries. The agent fetches details via tool call. This also aligns with the context-pruning strategy in `04-cost.md` §5.

### F14 — Post-patch staleness

After `ship`, the graph returns to `screen`. But the code has changed — every prior screening measurement is now stale. The spec never decided whether to re-screen.

**Fix:** re-screen only the workloads whose files the patch touched. Others keep their measurements. Cheap, and correct.

### F15 — Trust transfers unsafely across projects

A `select_related` fix approved 50 times may have been on projects with narrow tables. Applied to a project with a wide parent table, it trades queries for enormous payloads.

**Fix:** ledger keys include project shape characteristics, not just fix category. A new project starts at level 0 for every category until it has its own history, with cross-project history shown as advisory context rather than as earned autonomy.

### F16 — The human arrives too late

`interrupt_before=["ship"]` means the human reviews after grounding, screening, investigation, repair, and audit are all paid for. If they would have rejected the direction, the whole budget is gone.

**Fix:** add an optional interrupt after the finding audit — the human sees *"here is what I found and why, before I try to fix it."* Enabled at trust level 0, skipped at higher levels. Cheap insurance during development, and it is also the point where a human's domain knowledge is most useful.

---

## 6. Flow flaws

**No handling of interacting findings.** Two findings in the same file, fixed sequentially. The second patch is written against pre-first-patch source. We re-probe between fixes but never re-derive the evidence chain.

**Fix:** after each ship, invalidate any pending finding whose `context` files the patch touched. Re-investigate rather than repair from a stale chain.

**No representativeness check anywhere.** We optimize what we can run. If the runnable workload is a test fixture that does not resemble production usage, we optimize the wrong thing with full confidence and complete evidence.

**Fix:** this is the `unrepresentative` verdict in the finding audit. It is a partial fix — the agent still cannot know real traffic patterns. State this limitation in `07-use-cases.md`.

**Screening has no ordering rationale beyond magnitude.** A 10× win on a monthly batch job outranks a 2× win on the hottest endpoint under our current sort. We have no call-frequency information.

**Fix:** where the project has logs or metrics, read them. Where it does not, present findings ordered by measured magnitude and **state explicitly that frequency is unknown**, rather than implying a priority we cannot justify.

---

## 7. Revised build order

Changes to `00-BRIEF.md` §5:

| Step | Change |
|---|---|
| 1 | unchanged — lab bench |
| 2 | **add:** record-and-replay stub strategy (F11) |
| 3 | **add:** objective work-verification thresholds (F6) |
| 4 | unchanged — replay cache |
| **4b** | **new:** split persistent store from checkpointed state (F5) — before any agent |
| 5 | Explorer, **with provisional playbook writes** (F4) |
| 6–7 | Diagnostician, **with conditional exclusions** (F3) |
| **7b** | **new:** finding audit node (F2) — before the Surgeon exists |
| 8 | mode separation |
| 9 | **replace:** metastability gate becomes static detection plus permanent manual review (F1) |
| 10 | Surgeon, **test audited before patch** |
| 11 | Adversary, patch audit, ablation study |
| 12+ | unchanged |

**Note the ordering change:** the finding audit is built *before* the Surgeon. It is cheap, it catches the most damaging class of error, and building it first means the Surgeon never operates on unaudited claims.

---

## 8. What the audit did not resolve

Honest residue — problems identified without good fixes:

- **Shared blind spots.** Surgeon and Adversary use the same model family. Different vendors help; nothing eliminates it.
- **Representativeness.** We cannot know production traffic from a test environment. Only mitigated, never solved.
- **Correct measurement, wrong inference.** The finding audit catches some of this. Not all.
- **Fixture realism.** Synthetic data has synthetic shape. Skewed generation helps; it is not real data.
- **The agent cannot know what it does not know.** If a defect class has no primitive, no amount of agency finds it. This is why the primitive set matters more than the agent design — and why pass four of the validation should eventually happen.
