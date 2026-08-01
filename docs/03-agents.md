# Agent Specification

**Every agent, every tool, every guardrail — the implementation reference**

Companion to `system-reference-spec.md`, `capability-catalogue.md`, `performance-loss-taxonomy.md`.

---

## 0. What is deterministic, precisely

Earlier documents overstated this. The genuinely deterministic layer is five operations that decide nothing:

```
execute(cmd, cwd, timeout)   → stdout, stderr, exit_code, wall_time
time(fn)                     → seconds
count(hook_name)             → integer, with captured stack per event
diff(a, b)                   → identical | differs, with the diff
stats(samples)               → mean, stdev, slope, r²
```

That is a **lab bench**. Instruments that obey and report.

The six primitives from `capability-catalogue.md` — scaling, ablation, substitution, isolation, observation, temporal — are **agent-executed strategies that use those instruments**. Ablation is not a deterministic procedure: choosing what to cut, writing a stub with the right return shape, and interpreting the delta are all judgment. Only "run this and time it" is mechanical.

Everything else in this system is an agent.

---

## 1. Graph implementation

### 1.1 State

```python
from typing import Annotated, TypedDict
from operator import add

class State(TypedDict):
    project:     dict                    # fingerprint, adapter, workspace path
    workloads:   list                    # produced by Ground
    screening:   list                    # growth table, sorted by suspicion
    target:      dict | None             # current workload
    experiments: Annotated[list, add]    # append-only log
    chain:       dict | None             # proven cause
    attempts:    Annotated[list, add]    # surgeon attempts + failure reasons
    verdict:     str | None
    ledger:      dict                    # trust level per fix category
    budget:      dict                    # per-phase steps and euros remaining
    flags:       Annotated[list, add]    # items awaiting human
```

**`Annotated[list, add]` is load-bearing.** It makes LangGraph append rather than replace when a node returns that key. Without it the agent loses its own history — the most common bug when building these systems.

### 1.2 Nodes

| Node | Kind | Function |
|---|---|---|
| `ground` | agent | Explorer |
| `screen` | code | deterministic growth screening |
| `investigate` | agent | Diagnostician |
| `repair` | agent | Surgeon |
| `audit` | agent | Adversary |
| `ship` | code | ledger update, PR creation |

### 1.3 Routing

```python
def after_screen(s):
    return "investigate" if s["screening"] else END

def after_investigate(s):
    if s["chain"]:                       return "repair"
    if s["budget"]["experiments"] <= 0:  return "screen"
    return "investigate"

def after_audit(s):
    if s["verdict"] == "clean":          return "ship"
    if len(s["attempts"]) >= 3:          return "screen"
    return "repair"
```

Three functions. That is the entire control logic; everything else is agents working.

### 1.4 Compilation

```python
app = g.compile(
    checkpointer=SqliteSaver.from_conn_string("runs.db"),
    interrupt_before=["ship"],
)
```

Use `PostgresSaver` if running multiple campaigns concurrently.

### 1.5 The three durability features, and what each buys

| Feature | Concretely buys |
|---|---|
| Checkpointing | A 90-minute, 200-call run survives a crash at minute 85 |
| `interrupt_before` | Human approves on Thursday; state resumes intact |
| Time travel | Adversary breaks the fix → rewind to pre-patch checkpoint, keep Ground and Investigate results |

---

## 2. Explorer

### 2.1 Configuration

| | |
|---|---|
| Model | cheap/fast tier |
| Temperature | 0.3 |
| Loop | ReAct — think, act, observe |
| Step cap | 60 |
| Context | fingerprint, playbook, sliding window of last 20 action/observation pairs |

Cheap model because the steps are many and individually simple. Paying premium rates to run `ls` is waste.

### 2.2 Tools

```
shell(cmd)                  → stdout, exit_code   [denylist enforced]
read_file(path, lines?)     → text
list_dir(path)              → entries
http_request(method, url, headers, body)  → status, body, elapsed
db_query(sql)               → rows
docker(action, service)     → status
read_playbook(fingerprint)  → accumulated patterns
write_playbook(entry)       → confirmation
```

### 2.3 Prompt structure

```
ROLE      You get unfamiliar projects running. You are not optimizing
          anything yet.

GOAL      Produce a workload: something runnable, at controllable input
          size, that does real work, and can be reset between runs.

KNOWN     <framework fingerprint>
          <playbook entries for this fingerprint>

HISTORY   <last 20 action → observation pairs>

RULES     One action at a time. After each, read the result before deciding.
          If a playbook entry matches your situation, try it first.
          When something works that isn't in the playbook, write it there.
          If you cannot make the project do real work, say so and stop.
          Never report success when the workload touches no data.
```

### 2.4 Output schema

```python
class Workload(BaseModel):
    id: str
    invoke_spec: dict          # how to run it
    scale_spec: dict           # what to vary, and how
    fixture_recipe: list[str]  # commands that seed data
    reset_method: Literal["transaction", "snapshot", "restart"]
    baseline: dict             # timing and counters at reference n
    evidence_of_work: dict     # proof it touched real data
```

`evidence_of_work` is mandatory and exists to make "it ran but did nothing" structurally unreportable as success.

### 2.5 Guardrails

| Guardrail | Mechanism |
|---|---|
| No destructive shell | denylist: `rm -rf /`, `git push`, `dd`, package uninstalls |
| No external network | container egress blocked; localhost only |
| Workspace confinement | writes rejected outside the workspace path |
| No production | refuses to start unless DB URL matches the configured test pattern |
| Step cap | 60, then escalate |
| Progress check | 15 steps with no new information → escalate |

### 2.6 Failure modes

| Failure | Detection | Response |
|---|---|---|
| DB won't start | container exit | try playbook alternates, then abort |
| Every route returns 401/403 | repeated auth failures | read settings, mint credentials |
| No fixtures found | no factory module | synthesize from schema via FK walk |
| Workload touches no data | counters near zero | **report honestly, stop** |
| Reset doesn't restore | row counts drift | fall back to container restart |
| Scale param has no effect | metrics flat across n | wrong entity; retry with another |

### 2.7 Cost

40–120 calls unfamiliar; ~10 with mature playbook.

---

## 3. Screen (deterministic, no agent)

```python
for w in workloads:
    w.reset();  m10  = measure(w, n=10)
    w.reset();  m100 = measure(w, n=100)
    ratio = {k: m100[k] / max(m10[k], eps) for k in metrics}
```

Flags raised:

- **superlinear** — a metric grows faster than input (ratio > ~12 for a 10× increase)
- **high flat cost** — large constant cost regardless of n

Everything else is skipped. Sorted by estimated impact.

**Valid terminal output:** *"Screened N workloads. None show superlinear growth or unexplained constant cost. No optimization opportunity detected."* Most tools cannot say this and invent findings instead.

Cost: zero model calls.

---

## 4. Diagnostician

### 4.1 Configuration

| | |
|---|---|
| Model | strong tier |
| Temperature | **0.8 hypothesis generation / 0.0 result interpretation** |
| Loop | hypothesize → experiment → interpret |
| Step cap | 40 experiments |
| Context | full experiment log (never truncated), source of the suspect region, instrument list |

**The split temperature is a real design decision.** Hypothesis generation benefits from diversity — you want unusual explanations considered. Result interpretation must not vary — 8.24 seconds means the same thing every time. Two separate calls with two settings.

### 4.2 Tools

```
read_file(path, lines?)              → text
ablate(target, stub_returns)         → runs workload with target stubbed
scale(param, values)                 → runs at each value, returns metrics
count(hook, during)                  → integer + stacks
time(region)                         → seconds
substitute(target, replacement)      → runs with replacement in place
git_checkout(ref)                    → switches revision
```

All of these execute in **diagnostic mode**. Nothing they produce can ship.

### 4.3 Prompt structure

```
ROLE      You determine why software is slow, by experiment.
          Reading code generates hypotheses. Only measurement confirms them.

TARGET    <workload, screening result>

LOG       <every experiment: hypothesis, primitive, measurement, verdict>
          Includes exclusions. Do not re-test a rejected hypothesis.

CODE      <source of the region currently under suspicion>

TOOLS     <instrument list with signatures>

RULES     One experiment at a time.
          State the hypothesis before choosing the primitive.
          A finding requires a measurement. Never conclude from reading alone.
          Record exclusions — proving where the cost is NOT is valuable.
          When cause is proven, emit the evidence chain and stop.
```

### 4.4 Output schema

```python
class Experiment(BaseModel):
    hypothesis: str
    primitive: Literal["scaling","ablation","substitution",
                       "isolation","observation","temporal"]
    design: dict
    measurement: dict          # REQUIRED — no measurement, no experiment
    verdict: Literal["confirmed","narrowed","rejected"]

class EvidenceChain(BaseModel):
    symptom: dict
    exclusions: list[Experiment]
    localization: list[Experiment]
    mechanism: str
    complexity: dict           # measured growth per axis
    site: dict                 # file, lines, source
    context: list[dict]        # implicated files + why each
    confidence: float
```

Pydantic rejects any finding without a measurement. This is enforcement, not instruction.

### 4.5 Guardrails

| Guardrail | Mechanism |
|---|---|
| Diagnostic mode only | separate worktree; container destroyed on exit |
| No claim without measurement | schema validation |
| No repeated hypotheses | exclusion list in context |
| Experiment cap | 40, then emit partial chain |
| Progress check | 8 experiments with no narrowing → escalate |

### 4.6 Failure modes

| Failure | Response |
|---|---|
| All hypotheses rejected | emit exclusions — a proven negative is a real result |
| Ablation breaks the workload entirely | note as informative-but-unmeasurable, narrow the cut |
| Measurements non-reproducible | abort the branch, record the instability |
| Cause in a third-party dependency | report, do not patch |
| Cause is architectural | report with evidence, no patch |
| Multiple causes | emit separate chains; never batch |

### 4.7 Cost

10–30 calls per hypothesis; 4–8 hypotheses typical.

---

## 5. Surgeon

### 5.1 Configuration

| | |
|---|---|
| Model | strong tier |
| Temperature | 0.2 first attempt, 0.6 on retries |
| Attempts | 3, each must differ in approach |
| Context | evidence chain, implicated files, prior attempts **with failure reasons** |

Higher temperature on retries is deliberate: a retry at 0.2 tends to produce a variation of the same idea, which will fail the same way.

### 5.2 Tools

```
read_file(path, lines?)     → text
write_test(script)          → saves the falsification test
run_test(script, on_ref)    → pass/fail
write_patch(diff)           → staged in the candidate worktree
run_tests(subset?)          → suite result
run_workload(n)             → response + metrics
```

### 5.3 Mandatory ordering

```
1. write falsification test
2. run it against UNPATCHED code
3. it MUST fail — if it passes, stop and report; do not write a patch
4. only then write the patch
```

Rationale: a test that passes before you change anything is testing nothing. This gate costs one script and kills entire wasted branches.

### 5.4 Output schema

```python
class FalsificationTest(BaseModel):
    claim: str
    script: str
    failed_on_unpatched: bool     # must be True to proceed
    catches: list[str]            # named cheat classes

class Patch(BaseModel):
    diff: str
    files: list[str]
    approach: str                 # compared against prior attempts
    rationale: str
```

### 5.5 Guardrails

| Guardrail | Mechanism |
|---|---|
| Cannot edit tests, fixtures, harness | `apply_patch` rejects server-side |
| Candidate mode only | correctness-preserving worktree |
| Retry must differ | prior `approach` strings in context; validated |
| 3 attempts | then escalate with full history |

---

## 6. Adversary

### 6.1 Configuration

| | |
|---|---|
| Model | strong tier — **different vendor from Surgeon if possible** |
| Temperature | 0.9 |
| Rounds | 2, then escalate |
| Sees | original code, patched code, evidence chain, falsification test |
| **Never sees** | Surgeon's reasoning, rationale, or prior attempts |

Context isolation is implemented as a freshly constructed message list — not a prompt instruction to disregard.

Justification for isolation: reward-hacking research found 72% of exploit episodes carried explicit reasoning framing the exploit as legitimate. A reviewer sharing that context inherits the rationalization.

### 6.2 Tools

```
craft_input(spec)              → builds an adversarial fixture
run_workload(n, on_ref)        → response + metrics on either revision
diff_outputs(a, b)             → identical | differs
run_tests(subset?)             → suite result
read_file(path)                → original or patched only
find_callers(symbol)           → other call sites
```

### 6.3 Attack classes

| Class | What it tries |
|---|---|
| Equivalence | empties, nulls, duplicates, ties, unicode, boundaries, unordered results |
| Cheat | cached state, deferred work, over-fetch, stubbed response, shape-specific special-casing |
| Trade | memory, bytes, lock duration, latency, startup — what went up? |
| Scope | other callers of the modified symbol |
| **Test-quality** | **would a cheat pass the Surgeon's own test? if so, write the test that wouldn't** |

The last class is the deepest move in the design: the Adversary audits the verifier, not only the artifact.

### 6.4 Output schema

```python
class Verdict(BaseModel):
    result: Literal["clean","broken","suspicious"]
    attacks_run: list[dict]
    reproducing_input: dict | None    # required when broken
    concern: str | None               # required when suspicious
    strengthened_test: str | None     # when the surgeon's test was weak
```

### 6.5 Cost

10–25 calls per round; 1–2 rounds typical.

---

## 7. Safety layers

Every agent action passes all of these. They are independent, and **none of them asks the model to behave.**

| Layer | Mechanism | Prevents |
|---|---|---|
| Sandbox | container, no external egress, CPU/memory caps | runaway processes, exfiltration |
| Mode separation | distinct git worktrees; diagnostic worktree destroyed on exit | a deliberately-broken ablation ever shipping |
| Patch filter | `apply_patch` rejects diffs touching tests/fixtures/harness | the oldest cheat there is |
| Environment gate | refuses to start unless DB URL matches the test pattern | touching production |
| Budget | step and euro ceilings per phase | confident pursuit of a wrong idea |
| Progress check | no new information in N steps → escalate | silent loops |
| Schema validation | Pydantic on every output; reject and retry | malformed state corrupting the graph |
| Tool validation | allowlist + argument checking before dispatch | unexpected tool use |
| Human interrupt | `interrupt_before=["ship"]` at trust level 0 | everything else |
| Kill switch | budget exhaustion halts and checkpoints | cost runaway |

The design rule: **make misbehaviour impossible or ineffective, rather than discouraged.** A guardrail the model can talk itself past is a wish.

---

## 8. MCP placement

**Not in v1.** One Python adapter for Django, called as a function. A protocol between two components you wrote yourself is overhead.

**In v2**, at the second adapter in a different language. The interface:

```
discover_workloads()          → workload list
seed(n)                       → populate at scale
run_workload(id, n, mode)     → response, metrics, stacks
run_tests(subset?)            → result
read_source(path, lines?)     → text
apply_patch(diff)             → applied | REJECTED
reset_state()                 → clean
capabilities()                → available counters, hook points
```

A Rails developer implements this in Ruby, in their own repo. Your Python orchestrator connects without either side importing the other. That is the M×N problem MCP exists for.

Build step 11, not step 1.

---

## 9. Cost model

| Phase | First run | Mature playbook | Model tier |
|---|---|---|---|
| Ground | 40–120 | ~10 | cheap |
| Screen | 0 | 0 | none |
| Investigate | 40–240 | same | strong |
| Repair | 15–45 | same | strong |
| Audit | 20–50 | same | strong |
| **Total** | **~200–400** | **~100–250** | mixed |

Wall clock 60–120 minutes, dominated by seeding and workload execution, not by model latency.

Controls: cheap model for Ground; batch experiment planning into single calls; smallest N that shows the slope; hard euro ceiling per campaign.

---

## 10. Build order

| Step | Build | Proves |
|---|---|---|
| 1 | The five lab-bench operations, no AI | the instruments work |
| 2 | Ablation by hand on one Django project | the key primitive is viable |
| 3 | Explorer agent alone | **the riskiest component** |
| 4 | Screen (deterministic) | targets can be picked |
| 5 | Diagnostician, one primitive | evidence chains form |
| 6 | Diagnostician, second primitive | **agency matters — it switches instruments** |
| 7 | Mode separation | before any patch exists |
| 8 | Surgeon, test-first | fixes are verifiable |
| 9 | Adversary, isolated + ablation study | **the innovation, and whether it earns its cost** |
| 10 | LangGraph durability | survives a kill mid-run |
| 11 | Playbooks, ledger, second adapter → MCP | it generalizes |

Steps 1–5 are a useful system. Step 6 is the thesis. Step 9 is the contribution.
