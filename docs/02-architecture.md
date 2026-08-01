# System Reference Specification

**Repository in, verified patch out — every layer, every artifact, every failure mode**

Companion to `capability-catalogue.md`, `agentic-architecture.md`, `performance-loss-taxonomy.md`. This document supersedes the layer descriptions in `agentic-architecture.md` §2.

---

## 0. The pipeline at a glance

| Layer | Owner | Consumes | Produces | Mode |
|---|---|---|---|---|
| 1 Ground | Explorer agent | repo URL | workload object | diagnostic |
| 2 Investigate | Diagnostician agent | workload | evidence chain | diagnostic |
| 3 Repair | Surgeon agent | evidence chain | falsification test + patch | candidate |
| 4 Audit | Adversary agent | patch + test + chain | verdict | candidate |
| 5 Ship | Trust ledger (deterministic) | verdict + category history | PR or auto-merge | — |

Each layer's output is the *only* thing the next layer receives. No layer reads the repository directly except where its own evidence names a file.

---

## 1. Layer 1 — Ground

### 1.1 Objective

Convert an unknown repository into a **workload**: something runnable, scalable, resettable, and doing real work.

This is the hardest layer and the one that decides project viability. Build it second (after the measurement harness) and prove it before anything else.

### 1.2 Procedure

```
fingerprint      detect framework, version, ORM, database, test runner
load playbook    retrieve accumulated patterns for this fingerprint
stand up         database container, migrations, dependencies
enumerate        routes, CLI entry points, test cases, job handlers
attempt          invoke a candidate workload
diagnose failure auth? missing data? config? dependency?
resolve          apply playbook pattern or explore
seed             locate factories/fixtures, or synthesize from schema
verify work      confirm the workload touches real data
verify reset     confirm state can be restored between runs
verify scale     confirm input size is controllable
```

### 1.3 Output artifact

```
workload:
  id             stable identifier
  invoke()       → (response, wall_time, exit_status)
  scale(n)       → seeds n units of the primary entity
  reset()        → restores clean state
  baseline       response and timing at reference n
  fixture_recipe how the data was created, for reproducibility
  reset_method   "transaction rollback" | "db snapshot" | "container restart"
```

### 1.4 Agent tools

`shell`, `read_file`, `list_dir`, `http_request`, `db_query`, `run_tests`, `container_control`, `read_playbook`, `write_playbook`.

### 1.5 Failure modes

| Failure | Detection | Response |
|---|---|---|
| Cannot stand up the database | container exits | try alternate config from playbook, then abort with diagnostic |
| Auth blocks every route | 401/403 on all attempts | read settings, mint credentials; abort after step budget |
| No fixtures, no factories | no factory module found | synthesize from schema by walking FK chains |
| Workloads run but touch no data | all metrics near zero | **report honestly and stop** — never report "no issues found" |
| Reset does not restore state | row counts drift between runs | fall back to container restart; slower but sound |
| Scale parameter has no effect | metrics flat across n | wrong entity chosen; retry with a different one |
| Repo has no runnable form at all | exhausted budget | abort with a clear statement of what was tried |

### 1.6 Cost

40–120 model calls on an unfamiliar framework. ~10 with a mature playbook. Use the cheap model here — the steps are many and individually simple.

---

## 2. Layer 2 — Investigate

### 2.1 Objective

Determine the cause of cost, whatever kind of cost it is, and prove it by experiment.

### 2.2 The loop

```
form hypothesis      what could explain the observed cost?
select primitive     which of the six would test it? (see capability-catalogue)
design experiment    what varies, what is held constant, what is measured
execute              in diagnostic mode
read result          confirm, narrow, or reject
  reject  → new hypothesis, informed by the exclusion
  narrow  → new hypothesis, one level deeper
  confirm → emit evidence chain
```

**Hard rule: no claim without a measurement.** A hypothesis supported only by reading code is not a finding and may not be emitted.

**Hard rule: exclusions are recorded.** "Not the database, queries flat at 7,7,7 across 100× scale" is as valuable as the positive finding, and it is what stops the loop revisiting dead branches.

### 2.3 Primitive implementations

| Primitive | Mechanism | Notes |
|---|---|---|
| Scaling | `workload.scale(n)` at 3+ points, fit metric vs n | subtract framework baseline at n=0 |
| Ablation | monkeypatch / stub / early-return the component | **diagnostic mode only** — breaks correctness |
| Substitution | swap implementation or config value, re-measure | reversible; safest primitive |
| Isolation | run component standalone vs in full context | delta is contention |
| Observation | attach counter hook, capture stack per event | narrowest, cheapest |
| Temporal | check out earlier commit, re-run same workload | requires the workload to exist historically |

### 2.4 Output artifact — evidence chain

```
evidence_chain:
  symptom      metric and magnitude at reference scale
  exclusions   [ {hypothesis, primitive, measurement, verdict: rejected} ]
  localization [ {scope, primitive, measurement, share_of_cost} ]
  mechanism    plain-language description of the cause
  complexity   measured growth relationship, on each varying axis
  site         file, line range, source
  context      files the evidence implicated, with reason for each
  confidence   derived from number of independent confirmations
```

Every link carries the experiment that produced it. This artifact is simultaneously the input to Layer 3 and the body of the eventual pull request.

### 2.5 Failure modes

| Failure | Response |
|---|---|
| All hypotheses rejected | report the exclusions — a proof of *where the cost is not* is a real result |
| Ablation breaks the workload entirely | informative but unmeasurable; note and try a narrower ablation |
| Measurements non-reproducible across runs | abort the branch; record the instability |
| Cause is in a third-party dependency | report, do not patch |
| Cause is architectural | report with evidence; no patch attempted |
| Multiple independent causes | emit separate chains; never batch fixes |
| Budget exhausted mid-investigation | emit partial chain with what was excluded |

### 2.6 Cost

10–30 model calls per hypothesis. Typical investigation: 4–8 hypotheses. Use the strong model here — this is where judgment actually matters.

---

## 3. Layer 3 — Repair

### 3.1 Objective

Produce a patch that eliminates the measured cost while preserving behaviour.

### 3.2 Falsification test first — mandatory ordering

Before any patch is written:

```
falsification_test:
  claim        restatement of the mechanism from the evidence chain
  script       executable, asserting both cost and correctness
  must_fail    executed against unpatched code
  catches      enumerated cheat classes it is designed to detect
```

**Gate:** if the test passes on unpatched code, the hypothesis was wrong or the test is vacuous. Stop. Do not write a patch.

This inverts the usual order and it is the cheapest possible way to kill a bad branch.

### 3.3 Patch construction

Scope is determined by the evidence chain's `context` list, not by the agent's guess. Typical patches touch 1–3 files because the runtime named 1–3 files.

### 3.4 Retry discipline

| Attempt | Requirement |
|---|---|
| 1 | initial approach |
| 2 | must differ in *approach*, not parameters; failure reason in context |
| 3 | must differ again |
| 4 | escalate to human with full attempt history |

### 3.5 Cost

5–15 model calls per attempt. Strong model.

---

## 4. Layer 4 — Audit

### 4.1 Objective

Defeat the patch. Not review it.

### 4.2 Context isolation — non-negotiable

The Adversary receives: original code, patched code, evidence chain, falsification test. It **never** receives the Surgeon's reasoning, rationale, or prior attempts.

Justification: reward-hacking research found 72% of exploit episodes carried explicit chain-of-thought framing the exploit as legitimate problem-solving. A reviewer sharing that context inherits the rationalization.

**Recommended:** run the Adversary on a different model vendor than the Surgeon. Same family means shared blind spots. The ablation in §8 measures whether it matters.

### 4.3 Attack classes

| Class | Method |
|---|---|
| Equivalence | construct inputs where old and new outputs differ — empties, nulls, duplicates, unicode, boundary sizes, unordered results, ties |
| Cheat | is the improvement real? cached state, deferred work, over-fetch, stubbed response, shape-specific special-casing |
| Trade | what went up? memory, bytes, lock duration, latency, startup |
| Scope | who else calls the modified code? do they still pass? |
| Test-quality | **would a cheat pass the Surgeon's own test?** if so, write the test that wouldn't |

The last class is the design's deepest move: the Adversary audits the verifier, not only the artifact.

### 4.4 Verdicts

| Verdict | Consequence |
|---|---|
| `clean` | proceed to Layer 5 |
| `broken` | return to Surgeon with reproducing input |
| `suspicious` | escalate to human with the concern stated |

### 4.5 Cost

10–25 model calls per round. Typically 1–2 rounds.

---

## 5. Layer 5 — Ship

Fully deterministic. No model involvement.

### 5.1 Trust ledger

| Level | Entry condition | Behaviour |
|---|---|---|
| 0 | new fix category | human approves every patch |
| 1 | 10 approved, 0 rejected | auto-opens PR, human merges |
| 2 | 50 approved, <2% reverted | auto-merges behind a flag |
| — | any revert or rejection | demote one level |

Tracked per fix category, not globally.

### 5.2 Pull request contents

Before/after measurements on every varying axis; the full evidence chain including exclusions; guard metrics showing what did not regress; test results; the falsification test as a permanent regression test; the Adversary's verdict and any round-one reproducing cases.

**The PR carries its own proof.** This is what makes it reviewable in minutes.

---

## 6. Execution modes

| | Diagnostic | Candidate |
|---|---|---|
| Purpose | measurement | shippable change |
| Correctness | may be broken deliberately | must be preserved |
| Primitives allowed | all six | substitution only |
| Container | ephemeral, discarded | persistent within the attempt |
| Worktree | separate, never committed | the patch branch |
| Output | measurements only | diff + measurements |
| Diff escape | **structurally impossible** | reviewed |

Enforcement is in the harness — separate containers, separate git worktrees, diagnostic worktree destroyed on container exit. Never a prompt instruction.

---

## 7. Control plane

### 7.1 LangGraph state

```
project      fingerprint, adapter, playbook refs
workloads    registry with fixture recipes and baselines
chains       [ evidence chains, with status ]
current      chain under repair
duel         surgeon attempts, adversary verdicts, round count
ledger       per-category autonomy levels and history
budget       per-phase step counts, model spend, wall clock
flags        awaiting human decision
```

### 7.2 Budgets

| Phase | Cap | On exhaustion |
|---|---|---|
| Ground | 60 steps | abort with diagnostic |
| Investigate | 40 experiments | emit partial chain with exclusions |
| Repair | 3 attempts | escalate with history |
| Audit | 2 rounds | escalate |
| Global | euro ceiling | halt, checkpoint, report |

Every phase also runs a progress check: if the last N steps produced no new information, escalate rather than continue.

### 7.3 Why durability is load-bearing

A single investigation ran ~90 minutes and ~180 model calls in the worked example. Crash recovery, multi-day human approval gates, and checkpoint-rewind on adversary rejection (preserving Layers 1–2) are all requirements, not conveniences.

---

## 8. Evaluation

| Metric | Method | Why it matters |
|---|---|---|
| **Diagnostic agreement** | run Layer 2 ten times on one repo; measure agreement on primary finding | the honest form of "nearly deterministic"; nobody publishes this for this domain |
| Capability | SWE-Perf's 140 instances, reported per category | comparable against expert patches |
| Learning curve | steps-to-first-workload vs projects seen | proves memory works; should decline |
| Adversary value | ablation: run with and without; count bad patches reaching a human | if the delta is small, cut the Adversary |
| Cost | euros per confirmed finding | should decline with playbook maturity |
| Failure catalogue | publish repos where nothing was found, caught cheats, flipped diagnoses | more credible than a success rate |

---

## 9. Build order

1. **Measurement harness, no AI.** Ablation first (simpler and more general than counters), then scaling, then counters. Print tables.
2. **Explorer agent, alone.** Unfamiliar repo → runnable workload. Hardest component; prove viability here.
3. **Diagnostician, one primitive.** Must emit an evidence chain with measurements.
4. **Second primitive.** The agent must switch instruments when the first comes back flat. **This is the step where the project becomes about agents.**
5. **Execution mode separation.** Before any patch is ever generated.
6. **Surgeon**, falsification test first.
7. **Adversary**, isolated context. Run the ablation immediately.
8. **LangGraph** — checkpoints, interrupts, time travel.
9. **Playbooks and memory.** Measure the learning curve.
10. **Trust ledger.**
11. **Second framework adapter → extract MCP.**

Steps 1–3 are a useful system. Step 4 is the thesis. Step 7 is the innovation.

---

## 10. Viability checks to run this week

Before committing, three cheap experiments that could each invalidate the approach:

1. **Can you ground real repos?** Take three arbitrary Django projects from GitHub. Can you stand them up with a populated database and hit one endpoint doing real work? If this is hard by hand, it is much harder for an agent.
2. **Does ablation work in Python?** Monkeypatch a serializer method in a running Django app, measure the delta. One afternoon.
3. **Is reset reliable?** Can you seed, run, roll back, and get identical row counts across ten cycles? Everything downstream assumes this.

If all three work, the architecture is sound and the rest is engineering. If any fails, it changes what you build — and you want to know now.
