# Cost Engineering

**Cutting cost ~90% without losing quality**

Companion to `agent-specification.md`. Every technique here is either identical tokens, validated substitution, or work not done twice. None trades accuracy for price.

---

## 0. Why this matters beyond your budget

An agentic tool that costs $40 per run is a demo. One that costs $2 is something a team can put in CI and run on every pull request. **The cost engineering is what makes the product adoptable**, not just what makes the thesis affordable.

Enterprises ask this before they ask about accuracy.

---

## 1. Where the money actually goes

Per run, roughly 250 calls, but the distribution is skewed:

| Agent | Calls | Context | Output | Naive cost |
|---|---|---|---|---|
| Explorer | ~100 | 8k, stable | 500 | $16 |
| Diagnostician | ~80 | 20k→60k, growing | 1k | $13 |
| Surgeon | ~30 | 15k | 2k | $5 |
| Adversary | ~40 | 15k | 1.5k | $6.50 |

Reference rates as of mid-2026: frontier ~$5 input / $25 output per million tokens, mid-tier ~$2/$6, cheapest paid ~$0.03/$0.13. Cached reads bill at 0.1×. Batch API is 50% off. **Verify current rates before budgeting** — these move.

Four levers: fewer calls, fewer tokens per call, cheaper rate per token, don't pay twice for the same tokens.

---

## 2. Technique 1 — Route by step, not by agent

The central insight. Inside every agent there are two kinds of step.

**Creative** — few calls, genuinely need the strong model:

| Step | Agent | Calls/run |
|---|---|---|
| Form a hypothesis | Diagnostician | ~15 |
| Choose a fix approach | Surgeon | ~5 |
| Design an attack | Adversary | ~10 |

**Mechanical** — many calls, mid or cheap model performs identically:

| Step | Agent | Calls/run |
|---|---|---|
| Decide next action from a command result | Explorer | ~100 |
| Write an ablation stub | Diagnostician | ~25 |
| Interpret a growth table | Diagnostician | ~40 |
| Write the patch from a chosen approach | Surgeon | ~25 |
| Execute and diff an attack | Adversary | ~30 |

Routing whole agents means paying frontier rates for ~220 of 250 calls that don't need it. Routing by step means paying for ~30.

**Implementation:** each agent exposes two call paths with different model bindings. Not two agents — one agent, two client configurations.

---

## 3. Technique 2 — Cascade wherever a deterministic validator exists

This is what makes "no quality loss" honest rather than aspirational.

Try cheap. Check mechanically. Escalate on failure.

| Step | Mechanical check | Cascade safe? |
|---|---|---|
| Explorer action | command exit code | **yes** |
| Ablation stub | does it execute | **yes** |
| Patch | test suite passes | **yes** |
| Falsification test | fails on unpatched code | **yes** |
| Evidence chain | schema requires a measurement | **yes** |
| Attack execution | outputs differ or don't | **yes** |
| **Hypothesis generation** | **none exists** | **no — pay full price** |
| **Attack design** | **none exists** | **no — pay full price** |

Where a machine can catch a wrong cheap answer, the cheap model costs only an occasional retry. Where nothing can catch it, don't gamble — a bad hypothesis wastes an entire investigation branch, which costs far more than the model upgrade.

**Escalation policy:** 2 cheap attempts, then strong. Log the escalation rate per step type — if a step escalates more than ~30% of the time, promote it permanently.

---

## 4. Technique 3 — Prompt caching (the biggest single win)

The Diagnostician's context is an ideal caching shape: long stable prefix, small growing suffix.

```
[ system prompt        ]  stable across the whole run
[ playbook entries     ]  stable
[ source under study   ]  stable within a hypothesis
[ experiment log       ]  APPEND-ONLY
[ current question     ]  varies
```

At 0.1× for cached reads, input cost on the most expensive agent drops ~85%. **The tokens are identical** — there is no quality question here at all.

**Critical constraint this imposes on the design:** the experiment log must be append-only in the prompt. Never reorder it, never re-summarize it mid-investigation, never insert anything above it. Any of those invalidate the cache and you pay full price on every subsequent call in the loop.

This is a cost consideration dictating an architectural rule, and it is worth naming as such.

---

## 5. Technique 4 — Summaries in context, details on demand

The experiment log grows. But the agent does not need forty full stdout dumps preloaded.

```
in context:   experiment 7 — ablation of get_discount_price
              → 8.24s becomes 1.11s. 87% of cost localized.

on demand:    read_experiment(7) → full output, stack traces,
                                    per-call timings, raw counters
```

Nothing is discarded — it is stored and retrievable via a tool call. Context drops 60–80%. The agent pulls detail in the rare case it needs it.

**Zero information loss**, which is the difference between this and naive truncation.

---

## 6. Technique 5 — The replay cache

Experiments are deterministic. Cache them.

```
key:    (repo_sha, workload_id, experiment_spec, fixture_hash)
value:  full measurement result
```

**During development this is transformative.** Debugging the Surgeon means replaying a recorded investigation in seconds at zero token cost, instead of re-running ninety minutes of grounding and experiments. It changes iteration speed from ~5 cycles a day to ~50.

**In production** it removes duplicate work across retries and across findings in the same repo.

Build this in the first week. It is the highest-leverage engineering after the Explorer itself.

---

## 7. Technique 6 — Structured output

Output tokens cost 3–6× input. Force JSON schemas rather than prose.

```
bad   "I think the issue here is likely related to the way the
       serializer handles... [400 tokens of explanation]"

good  {"hypothesis": "...", "primitive": "ablation",
       "target": "get_discount_price"}   [40 tokens]
```

Reasoning happens in thinking tokens; the output should be the decision, not an essay about it. Cuts output volume ~70% on structured steps.

---

## 8. Technique 7 — Batch API for anything not interactive

50% off, at the cost of latency. Evaluation runs are not latency-sensitive — a benchmark sweep runs overnight either way.

Applies to: benchmark evaluation, agreement studies, ablation studies, learning-curve measurement. Effectively everything that goes in the thesis.

Does not apply to: interactive development, human-in-the-loop moments.

---

## 9. Technique 8 — Don't start work you can avoid

Free stopping points, in order of how much they save:

| Gate | Cost | Saves |
|---|---|---|
| Screening (deterministic) | 0 tokens | eliminates ~70% of workloads before any agent runs |
| Falsification test must fail | 1 script | kills a whole repair branch before patch work |
| Progress check | 0 | stops a stuck loop before it burns its budget |
| Budget cap | 0 | hard ceiling per phase |
| Honest null result | 0 | ends the run instead of manufacturing findings |

Screening is the biggest. Nine workloads screened, two investigated — a 78% saving before any other technique applies.

---

## 10. Technique 9 — Playbook amortization

The Explorer is the highest-volume agent, and its work is the most repeatable.

| Project | Explorer calls |
|---|---|
| 1st Django project | ~120 |
| 10th | ~40 |
| 50th | ~10 |

A 92% reduction on your largest call volume, achieved by memory rather than model choice. **And the learning curve is a publishable result**, so the cost optimization doubles as a thesis contribution.

---

## 11. Technique 10 — Deduplicate within a run

Grounding happens once per repo, not once per finding. Baseline measurements are shared. Fixture seeding is reused across experiments at the same scale. Obvious, and easy to get wrong when each node re-derives what it needs.

---

## 12. The arithmetic

**Two corrections to earlier drafts of this document.** Previous figures quoted per-*finding* cost as though it were per-*run*, and underestimated context growth in the investigate loop. Both errors ran in the optimistic direction. Corrected below.

### 12.1 Worst case — every guardrail failing, budget caps holding

Assumes: all frontier model, no caching, no cascading, no playbook, context growing unbounded within the cap, and **five findings per repo**.

| Phase | Calls | Context | Output | Cost |
|---|---|---|---|---|
| Ground | 60 (cap) | 8k | 500 | $3.15 |
| Investigate | 120 per finding | **grows to 60k** | 1k | $39.00 |
| Finding audit | 10 per finding | 40k | 2k | $2.50 |
| Test audit | 5 per finding | 20k | 1k | $0.75 |
| Repair | 45 per finding | 20k | 3k | $7.88 |
| Patch audit | 50 per finding | 20k | 2k | $7.50 |
| | | | **per finding** | **$57.63** |
| | | | **per run (5)** | **~$291** |

**Project worst case:**

| | Runs | Cost |
|---|---|---|
| Development | ~800 (20% full, 80% early failure) | ~$50,000 |
| Evaluation | ~260 | ~$75,000 |
| **Total** | | **~€125,000** |

That number is not a budget. It is the reason the engineering below is mandatory rather than optional.

### 12.2 The dominant variable

**Context size in the investigate loop accounts for most of the worst case.**

| Investigate loop | Cost |
|---|---|
| 120 calls @ 60k context, uncached | $39.00 |
| 120 calls @ 12k pruned, 85% cached | $1.68 |

**23× from one variable.** If only one optimization ships, it is this one — context pruning plus an append-only prefix that caching can hit.

### 12.3 Engineered case

| Phase | Configuration | Cost per finding |
|---|---|---|
| Investigate, 15 creative | frontier, 12k pruned, 85% cached | $0.59 |
| Investigate, 105 mechanical | mid-tier, cached | $1.09 |
| Finding audit, 10 | mid-tier | $0.15 |
| Repair, 25 | cascade mid→frontier | $0.60 |
| Patch audit, 10 design + 30 execute | frontier / mid split | $0.55 |
| **Per finding** | | **~$2.98** |
| Ground, 10 calls | cheap model + mature playbook | $0.01 |
| **Per run (5 findings)** | | **~$15** |

With batch API on evaluation runs: **~$7.50 per run**.

**Project engineered:**

| | Cost |
|---|---|
| Development (replay cache + local models) | ~€200 |
| Evaluation (260 runs, batched) | ~€1,950 |
| **Total** | **~€2,150** |

**Ratio: ~60×.** The gap between worst case and engineered case is the whole argument for taking this document seriously.

### 12.4 Required guardrail not previously specified

**Findings per run must be capped.** A repo where screening flags thirty workloads has unbounded cost under the current design. Cap at 5, rank by measured magnitude, escalate the remainder to the human as a list.

Without this cap, every figure above is meaningless — the worst case is simply unbounded.

### 12.5 Wall clock, which is worse than the money

A five-finding run is ~1,200 model calls plus 200+ workload executions plus seeding: **3–4 hours**. At 260 evaluation runs that is over 1,000 hours of compute, or ~six weeks serially.

For day-to-day work the constraint is sharper: a 3-hour cycle means ~3 experiments a day. **This limits progress more than budget does**, and it is why the replay cache (§6) is the first thing to build.

---

## 13. Where quality genuinely could suffer

Honest accounting. Three places carry real risk:

| Risk | Mitigation | Residual |
|---|---|---|
| Cheap Explorer fails on unusual setups | cascade after 2 failures; log escalation rate | low — deterministic validator exists |
| Mid-tier misreads a measurement | schema requires the measurement itself; a wrong reading yields a failed experiment, not a wrong conclusion | low |
| Mid-tier Adversary misses an attack class | keep *attack design* on frontier; only *execution* is mid | **medium — measure this** |

The third is the one to watch. Run the Adversary ablation at both model tiers and report the difference. If mid-tier catches materially fewer cheats, promote it and accept the cost — that agent is the contribution and should not be compromised for €0.50 a run.

**Rule: never economize on a step whose failure is invisible.** Cheap models are safe exactly where a machine can catch them being wrong.

---

## 14. Build order for cost work

| When | Build | Why then |
|---|---|---|
| Week 1 | Replay cache | changes iteration speed immediately |
| Week 1 | Local model for development | removes dev spend entirely |
| Week 2 | Token counter + per-phase budget caps | you cannot optimize what you do not measure |
| Week 3 | Step-level model routing | biggest structural win |
| Week 4 | Prompt caching + append-only log | biggest token win |
| Week 5 | Context pruning with on-demand detail | compounds with caching |
| Later | Cascading with escalation logging | needs data on where cheap fails |
| Later | Batch API | only matters at evaluation scale |

Instrument spend per phase from day one and put the number in your final report. *"Found a 21× speedup for €2.30"* is a stronger claim than the speedup alone, because it tells a reader the tool is affordable to run on their own code.
