# 10 — IMPLEMENTATION BACKLOG

**Epics, stories, and acceptance criteria — build this ticket by ticket**

Read `00-BRIEF.md` first for scope and the authority map. This file is the execution plan; where it conflicts with the build order in `00-BRIEF.md` §5, this file wins because it is more granular.

---

## How to use this

**Story format:**

```
S-x.y — Title
Depends: prerequisite stories
Why: one line
AC: testable acceptance criteria
Notes: gotcha carried from the design docs
```

**Rules for whoever implements:**

- A story is done when every AC is demonstrably true, not when the code looks right.
- Every `Notes` line exists because a design pass found a failure mode. Do not skip them — they are the expensive knowledge in this document.
- Stories marked **SPIKE** produce a decision or a finding, not shippable code. Timebox them.
- Stories marked **SAFETY** must ship before any story that depends on them, without exception.

**Milestones:**

| Milestone | Epics | What exists at the end |
|---|---|---|
| M1 — Viability | E0 | Three cheap experiments that could invalidate the design |
| M2 — Useful without AI | E1–E5 | A tool that finds performance problems with zero model calls |
| M3 — The thesis | E6–E8 | An agent that switches instruments when a hypothesis fails |
| M4 — The contribution | E9–E12 | Adversarial verification and durable orchestration |
| M5 — Generalization | E13–E17 | Memory, adapters, evaluation, reporting |

**Stop at the end of M2 and reassess.** If the deterministic core cannot find real problems in real repos, no amount of agent work fixes that.

---

# EPIC 0 — Foundations and viability

**Goal:** resolve open decisions and run the three experiments that could invalidate the architecture.

**Why first:** each spike costs a day and any of them can change what gets built. Running them after two months of work is how projects die.

### S-0.1 — Repository scaffold
Depends: none
Why: everything else needs a home.
AC:
- Python 3.12+ project with `pyproject.toml`, dependency locking, and a package layout matching the epic structure (`bench/`, `primitives/`, `agents/`, `orchestrator/`, `adapters/`, `eval/`)
- `ruff` and `mypy` configured and passing on an empty project
- `pytest` runs and reports zero tests without error
- `README.md` states the project's one-line claim from `00-BRIEF.md` §1

Notes: Python is the right choice — the first adapter targets Django, and the instrumentation hooks are Python-native. Cross-language support arrives via MCP in E14, not by writing the core twice.

### S-0.2 — Architecture decision records
Depends: S-0.1
Why: seven decisions are currently implicit and will be re-litigated otherwise.
AC:
- ADR directory with one file per decision
- ADR-001: implementation language and why
- ADR-002: LLM SDK and provider strategy (including the different-vendor requirement for the Adversary)
- ADR-003: persistence — SQLite for checkpoints in dev, Postgres for concurrent campaigns; separate store for persistent data
- ADR-004: sandboxing approach (Docker vs alternatives)
- ADR-005: first target framework (Django + Postgres) and why
- ADR-006: how the tool tests itself (see S-0.7)
- ADR-007: the refusal list and its rationale, copied from `00-BRIEF.md` §3

Notes: ADR-002 must record that Surgeon and Adversary should run on different model vendors where possible. If that is deferred, record it as a known limitation rather than dropping it.

### S-0.3 — SPIKE: can we ground real repositories?
Depends: S-0.1
Why: **this is the highest-risk assumption in the entire design.**
AC:
- Pick three arbitrary Django projects from GitHub that are not tutorials
- For each, by hand: stand up a database, run migrations, seed data, hit one endpoint that returns real data
- Record for each: time taken, number of distinct obstacles, whether fixtures existed
- Write a findings note: which obstacles recurred, which were unique

Notes: if this is hard by hand it is much harder for an agent. If two of three fail, reconsider the target framework or the workload-discovery approach before building E7.

### S-0.4 — SPIKE: does ablation produce clean deltas?
Depends: S-0.3
Why: ablation is the most-used primitive; if its measurements are noisy the design's core is unsound.
AC:
- Monkeypatch a serializer method in a running Django app to return a recorded value
- Measure the endpoint 20 times patched and 20 times unpatched, interleaved
- Report coefficient of variation for each condition and whether the delta is statistically separable
- Repeat with an empty-collection stub and compare — do the two stub strategies give materially different numbers?

Notes: the second half of this AC is what motivates S-3.4's record-and-replay requirement. Confirm it empirically before building on it.

Result (2026-08-02, `spikes/S-0.4-ablation/FINDINGS.md`): confirmed, with a twist. The two stub strategies were indistinguishable on **timing** (p=0.64) while differing **6x in payload size** — so the premise holds but wall time alone cannot show it, because the ablated component was database-bound and the downstream work it fed was cheap. Timing-only measurement would have concluded the strategy does not matter and deleted S-3.4's recording requirement. Ablation delta itself was clean: 1454.73ms to 434.64ms, Cliff's delta -1.000, against a detection floor of ~20ms.

### S-0.5 — SPIKE: is state reset reliable?
Depends: S-0.3
Why: every experiment assumes a clean starting state.
AC:
- Seed, run a workload, roll back — ten cycles
- Assert row counts across all tables are identical every cycle
- Test with transaction rollback, and separately with a database snapshot restore
- Record which strategy is reliable and how long each takes

Notes: sequence counters, cached querysets, and connection-level state commonly survive a rollback. Check for those specifically.

Result (2026-08-02, `spikes/S-0.5-reset/FINDINGS.md`): **the first AC is too weak and would have shipped a broken reset.** Plain rollback kept row counts, content hashes and max ids identical across all ten cycles while leaving sequences permanently advanced (`helpdesk_ticket_id_seq` 509→759). Assert sequence values too, not just row counts. Rollback + explicit `setval` is correct at 19ms, against 163ms for a template copy and 2022ms for a dump restore — and needs no exclusive access, so unlike `template` it composes with concurrent experiments. The note is right about sequences and cached querysets; **Postgres session state does not survive a rollback** (`SET` is reverted on abort). The cached-queryset leak is untouched by every database-side strategy, so the reset contract has to cover process state as well.

### S-0.6 — Target repository selection
Depends: S-0.3
Why: development needs a fixed subject with known problems.
AC:
- One repository chosen and vendored or pinned by commit
- At least one known performance defect documented with its expected measurement signature
- A second repository chosen as a holdout, never used during development

Notes: the holdout matters — developing and evaluating against the same repo produces a tool that works on exactly one repo.

Result (2026-08-02, ADR 011, pins in `targets.toml`): target `django-helpdesk` @ `3a22901` — nested N+1 on `/api/tickets/`, 1193 queries for 100 tickets, scaling as `1 + T + F + T`, ablation ratio 0.29x against a ~20ms floor, **plus a second N+1 underneath it** (504 customfield queries, invisible until the first is ablated). Reserve `netbox` @ `4877d11` for S-17.3.

**The holdout is named in ADR 011 and `targets.toml`, and deliberately not here.** It was chosen because its endpoint is *already correct* — a holdout containing a defect only tests generalization, while one where the right answer is "nothing found" tests whether the system can resist manufacturing a finding. `tests/test_holdout_discipline.py` enforces that it stays out of everything except the files listed there, and it caught two real references while S-0.6 was being written — including this line's first draft. Reference the ADR rather than repeating the name.

### S-0.7 — Test strategy for the tool itself
Depends: S-0.2
Why: a system whose job is verification must be verifiable.
AC:
- Unit tests for the lab bench against synthetic programs with known complexity
- A fixture repository under `tests/fixtures/` containing deliberately planted defects: an N+1, a quadratic loop, an over-fetch, a slow import
- Golden-file tests for evidence chain serialization
- A mock LLM client that replays recorded responses, so agent logic is testable without API calls

Notes: the planted-defect fixture repo is the single most useful test asset in the project. Build it early and grow it whenever a real repo surprises you.

**Status (2026-08-02): partially delivered — one AC of four. The other three are blocked on code that does not exist, and the story should be split.**

Delivered: the planted-defect fixture repository, in `tests/fixtures/`. Six query defects and controls, five complexity functions, a slow/fast import pair, and 25 tests asserting every documented signature. It goes beyond the four defects listed above with two additions the spikes demanded — a **decoy** with a high constant query cost that must *not* be flagged (the netbox shape from S-0.3), and a component whose **downstream** work dominates, which is the case S-0.4 could not test and S-3.4 most needs. Every defect is paired with a control, because a detector that always answers "N+1" passes a fixture that only contains defects.

Blocked, with the blocker in each case:

| AC | Blocked on |
|---|---|
| Unit tests for the lab bench | **E1** — the lab bench does not exist. There is nothing to unit-test. |
| Golden-file tests for evidence chain serialization | **S-4.1** — the evidence chain schema is not defined, so there is no serialization to freeze. |
| A mock LLM client replaying recorded responses | **S-0.2 / ADR-002** — the SDK and provider strategy are undecided, and writing a mock against a guessed interface is the speculative abstraction `CLAUDE.md` forbids. |

The dependency line above (`Depends: S-0.2`) is also partly circular: S-0.2's ADR-006 is *"how the tool tests itself"*, which is this story's subject. ADR-006 should be written from S-0.7's outcome rather than before it.

**Recommendation:** split into `S-0.7a` (the fixture repository — done) and `S-0.7b` (lab-bench unit tests, golden files, mock LLM client), with S-0.7b resequenced to depend on S-0.2, E1 and S-4.1. Nothing is lost by waiting; the fixture repo is the piece E1 needs on day one, and it is available now.

### S-0.8 — SPIKE: can a model select the right instrument?
Depends: S-0.3, S-0.4, S-0.5
Why: **this is the project's central claim, and nothing in E0 tests it.** `00-BRIEF.md` §1 says the agent exists because *"choosing which one applies to a given program, sequencing them, and interpreting the results"* is the bottleneck. That claim is tested in exactly one place — S-8.7, *the thesis behaviour* — which sits behind E1 through E7.
AC:
- Present recorded measurement results from the E0 spikes as scenarios; the model chooses the next experiment
- Responses are constrained to a schema (conclusion, next instrument, whether a finding is warranted) so scoring is programmatic, not a reading of prose
- Every scenario carries a trap: an answer that is plausible and wrong
- Score separately: instrument selection, trap avoidance, and **refusal to manufacture a finding**
- Run each scenario N≥5 times to measure consistency, not a single lucky answer

Notes: no primitives are needed — the measurements already exist and are recorded, so this tests the *selection* step alone, which is precisely the named bottleneck. The decoy scenario is the sharp one: 37 queries, constant with dataset size, where a detector keying on "many queries" reports an N+1 that is not there. The noise scenario is sharper still — a 12.76ms shift with guard counters unchanged, where the correct answer is *no finding*, and a model that produces one has violated the invariant that null results are valid output.

Result (2026-08-02, **executed 2026-08-04**): scenarios and scoring harness in `spikes/S-0.8-instrument-selection/`; full write-up in its `FINDINGS.md`, raw per-run record committed at `results/selection.json`. **PASS on the pre-registered decision rule** — `claude-opus-5`, 6 scenarios × 10 repeats, 60 requests, ≈$2. **Trap avoidance 100% (30/30), finding discipline 97%.** The model never manufactured a finding: the decoy (37 queries constant across a 20× row change) produced `constant_per_request_overhead` and never an N+1, and the noise scenario (7.4ms shift inside a measured 12.76ms spurious-shift floor, guard counters byte-identical) produced no finding 10/10. The thesis scenario passed on both axes it exists to test — correct diagnosis 10/10 and the instrument switch away from query counting 10/10. **The result inverts E9's premise.** The finding audit was scoped against fabrication; across 60 runs that never occurred once. What occurred instead is persistent *under*-commitment — correct reasoning, withheld verdicts, and always one more experiment proposed. `none_report_no_finding` was chosen **0 times in 60**, including on the scenario where stopping is the only correct answer. So the risk E9 must address is **non-termination**, not invention, and the stopping decision likely cannot be the agent's own — S-5.4's budget halt bounds the damage without deciding sufficiency. **One scenario's criteria did not survive the run**: `post_ablation_residual` scored diagnosis 0%, but its evidence gives 504 customfield queries as a single-point measurement with no row count, from which an N+1 and a fixed per-request count are indistinguishable; the model answered `insufficient_evidence` 10/10 and chose the one instrument that separates them 10/10. Recorded as a criterion defect, and **the scenario was repaired rather than the criterion loosened**: the evidence now carries the scale sweep the model asked for (126/252/504 customfield queries against 25/50/100 rows, flat at ~5.04 per row), while `acceptable_diagnoses` stays at `('n_plus_one',)` — widening it to admit `insufficient_evidence` would have made the scenario unable to fail, and commitment is what it exists to test. Self-check re-confirmed 6/6, and the **re-run scored 100% on all four axes for $0.29**. Nothing about the model changed between the two runs, only the sufficiency of what it was shown. This is the second scorer-calibration defect in this spike's life (cf. `502d774`), which is itself a finding about curated scenario sets: five scenarios agreeing with the model reveal nothing about whether their criteria are right, and this one was caught only because the model disagreed. The harness also gained cost reporting, stdout flushing, and `--scenario`/`--out` — the first execution could not report its own cost, and a 16-minute run emitted nothing until it exited.

---

# EPIC 1 — The lab bench

**Goal:** five deterministic operations that measure faithfully and decide nothing.

**Why:** the agents reason about measurements. If the measurements are wrong, everything downstream is confidently wrong.

### S-1.1 — execute()
Depends: S-0.1
AC:
- `execute(cmd, cwd, timeout, env)` returns stdout, stderr, exit code, wall time
- Timeout kills the process group, not just the parent
- Output is captured without deadlocking on large writes
- Raises a typed error on timeout, distinguishable from a non-zero exit

**DONE (2026-08-02)** — `src/coldfix/bench/execute.py`, 10 tests in `tests/bench/test_execute.py`. All four AC met. A non-zero exit is returned as a *result*, not raised — only the absence of a usable result raises (`ExecutionTimeoutError`). `timeout` is required and keyword-only: a subprocess with no deadline can hang an investigation with no diagnostic. `env` replaces rather than extends, matching subprocess semantics, because a variable leaking in from the parent shell is how two runs of the same measurement come to differ. Process-group kill needed different code per platform — `start_new_session` + `killpg` on POSIX, `taskkill /T` on Windows. **The safety test was verified to discriminate**: sabotaging the kill to reach only the direct child makes the orphan survive and the test fail.

### S-1.2 — time()
Depends: S-1.1
AC:
- `time(fn, repetitions)` returns a list of durations using `perf_counter`
- Discards nothing automatically — warmup handling is the caller's decision
- Records whether the process was fresh or reused for each sample

Notes: do not build in a fixed warmup discard. Barrett et al. showed at most 43.5% of VM/benchmark pairs reach steady state, so "discard the first N" is an assumption that is wrong more often than not.

**DONE (2026-08-03)** — `src/coldfix/bench/timing.py`, 10 tests in `tests/bench/test_timing.py`. All three AC met. The note generalised into **ADR 012**: warmup is not the only decision `timeit` makes on the caller's behalf, and the other three are wrong here in the same direction — no batching (per-sample variance is what S-1.5's rank test consumes), no garbage-collection control (disabling it makes a patch that increases allocation pressure look free), and no `min`-taking (a workload that is pathological one run in five is a finding, not noise). `ProcessState.FRESH` is **scoped to the run** — it means no earlier sample of this run used this process, and claims nothing about the interpreter being newly started, because `time()` cannot observe that. `fresh_process_per_sample` is the caller declaring what it built; the test for it composes with `execute()` and checks the child pids are genuinely distinct rather than trusting the label. **The discard test was verified to discriminate**: sabotaging `time()` to drop the first sample fails it on `samples went missing`, along with three others.

### S-1.3 — count()
Depends: S-1.1
AC:
- `count(hook_name)` context manager returns an integer and a list of captured stacks
- Stack capture is optional and off by default (it is expensive)
- Counting overhead is verified to be under 5% of the counted operation's cost
- One test proves counts match an uninstrumented run's observable behaviour

**DONE (2026-08-03)** — `src/coldfix/bench/counting.py`, 17 tests in `tests/bench/test_counting.py`. All four AC met. **ADR 013**: this story is the mechanism, not the counters — S-3.6 defines those and S-14.1 makes hook points part of the adapter interface, so what ships here is the registry, the install/remove context manager, and stack capture. The decision worth not re-litigating is that `count()` **raises on an unknown hook name rather than returning zero**: zero is a publishable result in this system, so a misspelled instrument would otherwise produce a null finding indistinguishable from a real one. `calls_to()` refuses a `classmethod`/`staticmethod`/`property` rather than wrapping it, because a plain wrapper changes how the attribute binds — a correct count of a different program, which is the ADR 008 failure again. Overhead came in at **0.26µs per event against a 366µs operation (0.07%)**, but only after fixing a `Path.resolve()` — a filesystem call — that ran once per counted event on the capture path: 590µs per event, 132 seconds for the test file, and precisely the defect class this tool exists to find. Both AC-bearing tests were verified to discriminate by sabotage: dropping the `finally` fails only the raising-body removal test, and a wrapper that alters return values fails the uninstrumented-equivalence test.

### S-1.4 — diff()
Depends: S-0.1
AC:
- `diff(a, b)` compares JSON payloads and returns identical/differs plus a structural diff
- Order-insensitive comparison available as an explicit option
- Handles floats with a configurable tolerance
- Handles nested structures and returns the path to the first difference

Notes: order-insensitivity must be opt-in per comparison, decided by whether the original query had an ORDER BY. Defaulting to order-insensitive hides real regressions.

**DONE (2026-08-03)** — `src/coldfix/bench/diffing.py`, 28 tests in `tests/bench/test_diffing.py`. All four AC met. **ADR 014**: this is the one instrument that produces a verdict a patch is gated on rather than a fact someone reasons about, which inverts which failure matters — a false "differs" is visible and wastes a cycle, a false "identical" approves a patch and looks exactly like a correct approval. So everything is strict by default and each loosening is a named argument on a single call. The note's case generalised: `ignore_order` compares **multisets, not sets** (`[1,1,2]` vs `[1,2,2]` differ — the shape of a patch that duplicated one row and dropped another), and the other traps are all places where the obvious Python answer is unsafe — `True == 1` in Python but a boolean is not a number in JSON, `null` is not an absent key, a `str` satisfies `Sequence` but is not an array of characters, `bytes` is refused rather than read as integers, and two NaNs must agree or a payload containing one differs from itself. Paths are structural tuples rendered separately, so a key containing a dot cannot read as a separator. Both AC-bearing tests were verified by sabotage: defaulting `ignore_order` to true, and replacing the multiset with a set, each fail exactly the tests written for them. **Open debt**: unordered comparison *with* a tolerance is greedy and quadratic, because approximate equality is not transitive — conservative, but it wants a real bipartite matching if a workload ever needs it.

### S-1.5 — stats()
Depends: S-0.1
AC:
- Returns mean, median, stdev, coefficient of variation
- Fits a curve against a scale variable and returns slope, exponent, and r²
- Classifies growth as constant / linear / superlinear with a stated threshold
- Provides a rank-based significance test (Mann-Whitney U or Wilcoxon)

Notes: use a rank test, not a t-test. Timing distributions are not normal.

**DONE (2026-08-03)** — `src/coldfix/bench/stats.py`, 24 tests in `tests/bench/test_stats.py`. All four AC met, and **Epic 1's five instruments now exist**. **ADR 015**: three of the four AC are already in `statistics` (`fmean`, `median`, `stdev`, `linear_regression`, `correlation`), so the only gap was the hypothesis test — written out in ~40 lines rather than pulling in scipy for one function. That is "not yet", not "never": the USL fit in S-3.x needs non-linear least squares and that story can take the dependency. Correctness is checked against definitions rather than against itself — U against direct pairwise counting, and the p-value against an **exact permutation test** enumerating all 12,870 relabellings of two groups of eight. That cross-check produced measured limits: the approximation agrees within a few percent in the body, is conservative by ~10× in the far tail, and runs ~30% low on heavily tied data (the unsafe direction — tolerable only because tied metrics are counts, and counts are deterministic and read directly). Below 8 observations per group it **refuses** rather than returning an untrustworthy p-value; S-1.7 requires 20–30 anyway. A flat metric is handled before either fit is attempted, because "queries constant at 7,7,7" is the canonical exclusion this system exists to publish *and* the input a log-log fit degenerates on. Two fits are returned with separate r², since their disagreement is the superlinear signal. **Two of my own claims were wrong and were corrected by measurement**: that a rank test's p-value barely moves under an outlier (it moves an order of magnitude at n=10 — what does not move is the verdict), and that the tie correction stops ties inflating significance (it lowers the variance, so it raises significance).

**AUDIT (2026-08-03)** — the five instruments were run against inputs their tests did not cover, before S-1.6 was started. **ADR 016**. One serious defect: `rank_test([nan]*8, [1.0]*8)` returned **p = 0.0004** — a decisive, well-formed, fictional finding, because every comparison against NaN is false so ranking silently produces nonsense and every line downstream completes. Non-finite values are now refused by all three `stats` entry points. Three smaller ones: a missing binary, a bad `cwd` and an empty command raised three different untyped `OSError` subclasses and are now `ExecutionStartError`; a self-referential payload exhausted the stack and now raises `TooDeepError` at depth 200; and the drain after a timeout kill was unbounded, so a grandchild surviving the kill would hang `execute` forever inside its own timeout handler. One limitation is documented and pinned rather than fixed: `calls_to` cannot see a caller that did `from module import work`, and the undercount is silent — it matters less than it looks because nearly every real counter wraps a method, where the attribute is looked up on the class at every call.

**AUDIT 2 (2026-08-03)** — a second pass, asking what an *unseen repository* supplies that the fixtures do not: input that is well-formed and merely large. **ADR 017**. Three findings. `execute` captured output unbounded — a print loop handed back 80,400,000 characters, and memory exhaustion arrives before the timeout can fire, so the mechanism that bounds a runaway command could not help; each stream is now capped at 8,388,608 characters, head and tail kept, middle elided, count on the result. Reading is now bounded by the caller's deadline too, closing the case where the child exits but a grandchild holds a pipe and EOF never comes. `fit_growth` refused any metric that was zero at a scale — an ordinary count shape — and now returns the linear fit with `exponent`/`power_r_squared`/`growth` as `None`, never guessing growth from the line, because the thresholds are defined on the exponent. `stdin` is now `DEVNULL`, so a command that reads it gets EOF instead of spending its timeout on a prompt. Decoding moved in-process with an incremental decoder, since a 64 KiB read boundary can split a multi-byte character. The memory bound is asserted against peak allocation and **the test was verified to discriminate**: removing the drop fails it at 16 MB against a 100,000-character budget. Also fixed: `TooDeepError` was in `bench.__all__` but never imported, so `from coldfix.bench import *` raised.

### S-1.6 — Interleaved measurement sessions
Depends: S-1.2, S-1.5
Why: comparing against a stored baseline produces false positives.
AC:
- `compare(variant_a, variant_b, n)` alternates between conditions in randomized order within one session
- Never compares against a previously stored measurement
- Returns both distributions plus the rank test result
- A test proves that shuffling the order does not change the verdict on a known-equal pair

Notes: Laaber et al. found naive mean comparison produces high false-positive rates, and that same-instance randomized interleaving is what makes 10% differences reliably detectable.

**DONE (2026-08-04)** — `src/coldfix/bench/interleaving.py`, 15 tests in `tests/bench/test_interleaving.py`. All four AC met. **ADR 018**. The second AC is the one that needed code rather than convention, and the answer is the signature: `compare()` takes two **callables** and runs both itself, so a stored baseline — a list of numbers — has no parameter it fits. Not discouraged, unrepresentable, the same construction as `execute()` making `timeout` required. A runtime `TypeError` backs the annotation for callers that are not type-checked, which is the case that matters: an agent assembling a comparison from an artifact it read. The AC's "alternates ... in randomized order" has two readings and they protect against different things — a single shuffle of `n` A's and `n` B's can deal one condition into the first half by chance, which is the block design interleaving exists to replace, so this **randomizes within each round** and stays balanced across every prefix. Seed and the schedule as it ran are both on the result, because an experiment that cannot be re-run in its original order is not reproducible. `Sample.index` is the position in the *session*, not the variant's own run, since plotting against it is how drift becomes visible. No verdict field — instruments decide nothing, and the caller owns the threshold. `n` is floored at `MINIMUM_GROUP_SIZE` before anything runs, so a session cannot be spent discovering the rank test will refuse it. **The AC-bearing test was verified to discriminate, and its adversarial half is the interesting one**: `test_interleaving_cancels_a_drifting_machine` compares a function against *itself* on a machine whose cost grows every call and requires all twelve seeds to find nothing, while `test_a_block_design_manufactures_a_difference_on_the_same_work` runs the same workload the way this module refuses to and asserts p < 0.001. Sabotaging the schedule to a block design fails the first at **p = 0.0000 for a function against itself** — the Laaber false positive, reproduced. Removing only the shuffle correctly fails the randomization test and leaves the drift test passing, since strict alternation does still cancel monotonic drift. One wart found and fixed while testing: chaining through `time(fn, 1)` put a `TimingError` reading "sample 0 of 1" between `ComparisonError` and the real exception, so the intermediate is unwrapped.

### S-1.7 — Noise floor certification
Depends: S-1.6
Why: an auto-generated harness must prove it can see the effect it is hunting.
AC:
- Runs the baseline 20–30 times before any experiment
- Computes coefficient of variation and minimum detectable effect
- Refuses to proceed if the noise floor exceeds the target effect size, with a clear message
- Certification result is recorded in the experiment log

Notes: this is a novel contribution — no evolve-style framework certifies its evaluator before optimizing against it. Do not skip it as a refinement.

**DONE (2026-08-04)** — `src/coldfix/bench/certification.py`, 16 tests in `tests/bench/test_certification.py`. **Epic 1 is complete.** **ADR 019**. The decision worth not re-litigating: **the minimum detectable effect is simulated, not taken from a formula.** The textbook expression assumes normality, which is the assumption the rank test was chosen to avoid — using it here would reintroduce it one layer down. Instead the baseline is resampled into control and treatment, the treatment scaled by a candidate effect, and the pair put through `rank_test()` — *the same function the real comparison will call* — with the effect tightened by bisection until detection reaches 80% power. The returned value always had its power measured at or above target, never interpolated, so error falls on the conservative side. A refusal raises `NoiseFloorTooHighError` **carrying the certification**, following `ExecutionTimeoutError` carrying partial output: refusing by return value lets a caller ignore it, refusing without the evidence makes the refusal unloggable. **AC 4's dependency is missing and was handled deliberately, not improvised**: the experiment log is S-8.4 (depends on S-6.1 and S-5.7, does not exist), and building a second one now would guess at a schema S-8.4 already specifies — entries carry hypothesis, primitive, design, measurement and verdict, none of which a certification has. So `Certification` is a Pydantic model with stable field order and S-8.4 owns the appending; **confirmed with Allen before proceeding**. Two readings recorded: `n` has a floor of 20 and **no ceiling**, since "20–30" is guidance about sufficiency and refusing 60 samples would be refusing better evidence; and `alpha`/`power`/`trials` are constants recorded on every result rather than parameters. **Sabotage-verified**: making `certify()` never refuse fails two tests, and making the floor optimistically small — the dangerous direction, since it certifies a harness that can see nothing — fails five including the end-to-end check. The estimate is validated from a different seed with twice the trials (so it cannot pass by memorising its own resampling) and then end to end against `compare()`: an effect 4× the certified floor is found in 5 sessions of 5, one at ⅙ of it in at most 1 — both probabilistic, so asserting either on a single run would have been a flaky test dressed as a strict one. **One measurement worth keeping**: a bare busy-wait loop certifies at a floor of ~0.02%, which made the first end-to-end test meaningless because no effect was below the floor — real noise had to be injected to test the mechanism. A spin loop is a far better instrument than any workload this project will meet.

---

# EPIC 2 — Execution environment and safety

**Goal:** everything runs somewhere it cannot cause harm, and dangerous categories are refused before an agent ever sees them.

### S-2.1 — Sandboxed runner — **SAFETY**
Depends: S-1.1
AC:
- Every workload and experiment executes inside a container
- CPU and memory limits enforced
- No external network egress; localhost only
- Filesystem writes confined to the workspace
- Container is destroyed after each diagnostic run

**DONE (2026-08-06)** — `src/coldfix/sandbox/runner.py`, ADR 020, 57 tests in `tests/sandbox/` split into 39 that need no daemon and 18 marked `docker` that attempt each forbidden thing against a live one. All five AC met. **Two AC are not satisfied by the obvious construction, and both were found by writing the test rather than the code.** `--rm` does *not* destroy the container after each run: it fires when the `docker run` client exits cleanly, so the timeout path — the one case that matters — leaves the container running under the daemon, holding the workspace and consuming the CPU every later measurement is taken against. This is S-1.1's orphan problem moved one level up, where its process-group kill cannot see it, because the workload was never a process on this host. Removal is therefore forced by name in a `finally` on every path. Second, an exit code does not say whether a limit was *enforced*: an OOM kill arrives as SIGKILL and is indistinguishable from any other, and `docker run` exits 125 for its own failures, which is also a legal workload exit code. Both are resolved by `docker inspect` before removal — `.State.OOMKilled` separates a memory kill from any other kill, and the container's *absence* separates "docker never started one" from "the workload exited 125". An OOM kill raises rather than returns, on the S-1.1 rule that a truncated run is not a measurement. **The policy is not parameterised**: `Sandbox` has three fields and no argument that enables networking, adds a second bind mount, or lifts the read-only root, and a test asserts the field set itself so that widening it fails a test rather than merging quietly. **Sabotage-verified, four properties**: replacing `--network none` with `bridge` fails both egress tests; dropping the forced destroy fails six tests including three that check the daemon afterwards, and leaks exactly three containers; dropping `--read-only` fails both filesystem tests. The fourth is the honest one — **removing the `--memory-swap` pinning is caught only by the policy test, not by the live memory test**, because this WSL2 VM has no swap to hand out. That flag's necessity rests on documented docker behaviour rather than on anything this machine demonstrates, and a host configured with swap is where it would matter. Left open in ADR 020: `--cpus` is a quota rather than a `--cpuset-cpus` pinning, and the container runs as the image's default user, which on a Linux host leaves root-owned files in the bind-mounted workspace for S-2.2 or S-7.2 to own.

### S-2.2 — Git worktree management
Depends: S-0.1
AC:
- Create, list, and destroy worktrees programmatically
- A worktree can be created at an arbitrary revision
- Destroying a worktree removes all uncommitted changes
- Refuses to operate on a dirty main working tree

**DONE (2026-08-06)** — `src/coldfix/sandbox/worktrees.py`, ADR 021, 27 tests in `tests/sandbox/test_worktrees.py`, all against real repositories and real git. All four AC met. **The fourth AC needed a decision it does not state**: "refuses to operate on a dirty main working tree" does not say which operations count, and the two readings differ in what they protect. The guard applies to `create` — uncommitted edits live in no commit, so a worktree at HEAD does not contain them and every finding would cite code differing from the user's copy — and **deliberately does not apply to `destroy` or `list`**. Applied to removal the argument inverts: a main tree that went dirty mid-investigation would strand a worktree full of ablated source, which is the exact outcome ADR 004 exists to prevent, in the name of safety. Refusing to *list* would block a caller trying to discover what needs cleaning up because something needs cleaning up. The non-obvious direction is asserted by its own test so that tightening it into a safety regression fails. Untracked files count as dirty and ignored files do not, since `git status --porcelain` omits the latter and a repository with build output stays measurable; the two are carried separately on the error because `stash` fixes one and not the other. A worktree *inside* the main tree is refused though git permits it — it would appear there as untracked content, making the tree dirty and making every later `create` refuse, the module disabling itself by having run once. Removal is verified against the filesystem rather than git's exit code, since `remove --force` can report success and leave files something still holds — routine on Windows, and possible everywhere once S-2.3 bind-mounts a worktree into a container. **Sabotage-verified, and the sabotage found a real gap in the tests.** Dropping `--force`, skipping the survival check, and applying the guard to `destroy` each fail their test. Dropping `--detach` failed nothing — because the revision is resolved to a SHA before git is invoked, so git never sees a branch name. The first test set could not have detected this at all: every case used `main`, which git refuses to check out in a second worktree, so its refusal masked the flag. A branch checked out nowhere was added to the fixture, and even then detachment survives removing either mechanism alone — **it has two independent sufficient guarantees, and only removing both attaches the worktree**. Recorded in ADR 021, because it also means neither test is evidence that either mechanism individually works. Left open: nothing prunes a worktree whose run crashed; `Worktree.prunable` reports the state and acting on it belongs to whoever owns run lifecycle.

### S-2.3 — Execution mode separation — **SAFETY**
Depends: S-2.1, S-2.2
Why: ablation deliberately produces broken code; it must be structurally incapable of shipping.
AC:
- Two modes exist: `diagnostic` and `candidate`
- Each mode uses a distinct container and a distinct worktree
- A diff produced in diagnostic mode has no code path by which it becomes a patch — verified by a test that attempts it and fails
- Diagnostic worktrees are destroyed on container exit
- Mode is a required argument on every execution call, with no default

Notes: this is enforcement, not convention. A test must actively attempt the violation and assert it is impossible.

**DONE (2026-08-06)** — `src/coldfix/sandbox/modes.py`, ADR 022, 20 tests in `tests/sandbox/test_modes.py` written as attacks. All five AC met. **The enforcement is an absent method, not a rejected call.** `02-architecture.md` §6 gives the spec as a table whose decisive row is *Output — measurements only*, which is a claim about what diagnostic mode **can do**, not about what it is permitted to do. `DiagnosticSession` therefore exposes exactly `run`, `close`, `mode` and `worktree`; there is no `diff` to call, no argument to pass and no flag to set. A test asserts that public surface **by name as an exact set**, so adding any accessor fails it. **Three independent things must fail before a diagnostic change can ship**: there is no method; there is no repository inside the container, because a linked worktree's `.git` is a file naming a path in the main repo's `.git/worktrees/` and S-2.1 mounts exactly one directory, so git there has nothing to read; and there is no worktree afterwards, since closing destroys it and S-2.2 verifies that against the filesystem. **The second was not designed — it falls out of S-2.1's single mount, was found by checking, and is now asserted so that mounting a second directory later fails a test in this file.** That test asserts the metadata's absence directly rather than running `git diff` in the container, because `python:3.12-slim` ships no git at all and the naive test would pass for the wrong reason and keep passing if the metadata were later mounted. **AC 5 was ambiguous and the user chose the stronger reading**: mode is required with no default at `Workbench.open` and selects which of two *types* is returned, rather than being a per-call argument two calls on one session could disagree about. **Sabotage-verified one route at a time**: adding a `diff` to `DiagnosticSession` fails 2 tests, making `close()` stop destroying fails 3, giving `mode` a default fails 1, pointing both modes at one worktree fails 2 — and is additionally impossible, since S-2.2's `create_worktree` refuses an existing path, unplanned defence in depth now recorded so it is not removed by accident. Left open: container persistence "within the attempt" from the architecture table is **not** implemented — S-2.1 destroys every container after every run, so a candidate attempt re-enters a fresh container per command and reinstalls anything it needs. Reusing one is a caching change to a hot path, which `CLAUDE.md` requires be driven by a measured cost rather than a table row.

### S-2.4 — Protected path enforcement — **SAFETY**
Depends: S-2.2
AC:
- `apply_patch(diff)` rejects any diff touching test files, fixtures, conftest, the harness, or instrumentation
- Rejection happens server-side in the patch applier, never by instructing a model
- Protected paths are configurable per project but have safe defaults
- A test proves a patch modifying a test file is rejected

Notes: environmental hardening of this kind reduced exploit rates by 87.7% relative in the Reward Hacking Benchmark. This single gate does more work than any detector.

**DONE (2026-08-06)** — `src/coldfix/sandbox/patching.py`, ADR 023, 35 tests in `tests/sandbox/test_patching.py` written as attempts, reached through `CandidateSession.apply_patch`. All four AC met. **The obvious implementation is a complete bypass, and it was found before writing code by constructing the attack.** Asking git what a patch touches — `git apply --numstat` — **reports a rename by its destination only**: a diff renaming `tests/test_slow.py` to `src/harmless.py` reads as touching one unprotected path, so the test suite is deleted and the filter reports success. `git apply --summary` knows about the rename but compacts the paths (`rename tests/deep/{test_a.py => test_renamed.py} (100%)`), which cannot be split reliably. **So the diff is parsed here, taking both sides of every rename and copy, and git is consulted only as a cross-check** — if git reports a destination the parser did not find, the patch is refused as unparsable, because the filter must be a superset of git's view or it is not a filter. **The parser tracks hunk line counts** rather than scanning prefixes: inside a hunk every line starts with a space, `+`, `-` or `\`, so a removed line beginning `-- a/x` renders as `--- a/x` and is indistinguishable from a header to anything simpler; this removes both the missed header and the documentation file that quotes a diff being read as touching it. Matching is **case-insensitive** (Windows and macOS resolve `Tests/` and `tests/` to one file, so a case-sensitive rule is bypassable by one letter) and `**` spans segments while `*` does not cross a `/` — written out because `PurePath.full_match` is 3.13+ and because `fnmatch` over a whole path would silently protect `src/latest/thing.py`. **Every ambiguity resolves toward refusing**: a quoted path is rejected rather than decoded, since a filter that decodes git's C-quoting almost correctly would match a protected path as a different string than it names on disk. The audit runs before anything is written and rejection is of the whole patch, because a partial application leaves the source edited and the test untouched, which is indistinguishable from a fix that works. **Sabotage-verified on five properties, and one exposed a bad test**: dropping rename-source parsing fails 2, applying before auditing fails 2, skipping the escape check fails 4, letting `*` cross a `/` fails the `latest/` case — and making matching case-sensitive **failed nothing**, because the test used `Tests/test_speed.py` where `**/test_*.py` matches the filename whatever case the directory is in, so the directory rule was never exercised. Rewritten to use `Tests/helpers.py`, it now fails under the sabotage. **Second time in Epic 2 a test passed for a reason other than the one it claimed** (cf. ADR 021), both found by sabotage rather than review. Left open: symlinks — a patch editing a file that is a symlink to a protected path is not detected here, and the current protection is git's own refusal to follow symlinks when applying (since 2.32, after CVE-2021-21300), which is worth testing rather than inheriting once S-7.x stands up repositories this system did not create.

### S-2.5 — Production guard — **SAFETY**
Depends: S-0.1
AC:
- The system refuses to start unless the database URL matches a configured test pattern
- The check runs before any other initialization
- The error message states exactly what was expected and what was found
- No override flag exists

**DONE (2026-08-06)** — `src/coldfix/sandbox/production.py`, ADR 024, 44 tests in `tests/sandbox/test_production.py`. All four AC met, with one honestly qualified — see the last paragraph. **The check is the constructor**: `VerifiedDatabase(url)` either returns a verified handle or raises, so there is no unverified handle to hold, no `verify()` a caller could forget, and no ordering to get wrong. Downstream code takes a `VerifiedDatabase` rather than a string, so an unverified database cannot be *described*, let alone connected to — the same construction as ADR 022, where the enforcement was an absent method and here is an absent unverified state. **Default-deny on scheme, host and name.** A denylist of production-looking things fails the first time somebody names a database something it did not anticipate, silently, in the direction of destroying data. **The name check is load-bearing and the host list is the weak one** — `localhost` is one SSH tunnel from anything and a production compose file may call its service `db` too; what actually separates them is that people name production databases after the product. **No override exists, including the ones spelled as configuration**: no `force`, no `allow_production`, no environment variable, and a policy that would admit everything (`*`, or an empty pattern list) is refused at construction, because that is an override flag with a different name. The refusal **redacts the password** and `VerifiedDatabase` defines its own `__repr__`, since a frozen dataclass renders every field and this object is designed to be logged — a guard that refused the production database while printing its credential would be its own incident. Named `VerifiedDatabase` rather than `TestDatabase` because pytest tried to collect the latter as a test class. **Sabotage-verified on five properties, and two of the sabotages taught a lesson about sabotage**: dropping the name check fails 6 tests, a host denylist fails 2, a vacuous policy fails 1, removing the custom `__repr__` fails the leak test, un-redacting the error fails its own — but **the last two first reported no failures and both were wrong**, because `@dataclass` does not overwrite a `__repr__` defined in the class body, and a `sed` pattern containing `\n` does not match a literal backslash-n in source. A sabotage reporting no failures must be checked for having applied, or it reads as evidence for a property it never tested. **AC 1 is qualified: there is no runnable entry point yet** — nothing in this repository starts, E6 owns the orchestrator. What exists is the stronger half, that no database handle can exist without the check having passed; an entry point built later gets the refusal for free because it has nothing else to construct. Also open: an SSH tunnel from `localhost:5432` to production is undetectable by any URL-pattern check, and only an honestly-named database would be caught.

### S-2.6 — State reset strategies
Depends: S-0.5, S-2.1
AC:
- Three strategies implemented: transaction rollback, database snapshot restore, container restart
- Strategy is selectable per project and recorded in the workload artifact
- Each strategy is verified by the harness in S-2.7 before use

**DONE (2026-08-06)** — `src/coldfix/sandbox/reset.py`, ADR 025, 17 tests in `tests/sandbox/test_reset.py` against a real Postgres 16 container, marked `postgres`. Adds `psycopg[binary]` as the first runtime dependency after pydantic. All three AC met. **The first strategy as named does not work, and S-0.5 already proved it**: plain rollback failed 10/10 cycles while passing the check that story specified — row counts, content hashes and every `max(id)` identical, and `helpdesk_ticket_id_seq` 509→759, the workload's insert count accumulated and never returned. Postgres sequences are non-transactional by design (`nextval` must not roll back, or concurrent transactions could share an id), which is correct behaviour and precisely why it defeats a naive reset. So the strategy is **`ROLLBACK_AND_RESTORE_SEQUENCES`, and the name is part of the fix** — a test asserts it, so simplifying back to "rollback" has to argue with a failing assertion. Cost is 19.2 ms against 0.4 ms broken, and it is the only strategy needing no exclusive access. A never-used sequence restores to `start_value` with `is_called` false, since `pg_sequences.last_value` is NULL until first use and defaulting it to 1 makes the first real `nextval` return 2 — the same defect one row smaller. `SNAPSHOT_RESTORE` is a template copy at 163 ms that undoes schema changes and work another connection committed; `CONTAINER_RESTART` destroys server and storage and reseeds from SQL text rather than a dump, because `pg_restore` needs a version-matched client binary that S-0.5 found reports errors it then ignores. **The central test is the control**: `test_rollback_alone_leaves_sequences_advanced` reproduces the defect on a live server and asserts the *broken* behaviour, so its partner proves something. **Rollback's precondition cannot be checked here and is left to S-2.7**: it undoes work on its own connection, and a containerised workload commits on its own — a connection cannot see what another committed and attribute it. That makes `SNAPSHOT_RESTORE` the realistic default for container-driven workloads, and is the strongest argument for the "verified before use" criterion. **Sabotage-verified on four properties, and one corrected a false claim in this codebase**: removing the sequence restore fails 3 tests, mishandling a never-used sequence fails 1, skipping the reset after a raise fails 1 — and **dropping `--volumes` failed nothing, because the comment explaining it was wrong**. The Postgres image uses an *anonymous* volume, so a rebuilt container gets fresh storage either way and the reset is correct regardless; what `--volumes` prevents is every cycle stranding a full data directory nothing reclaims, which fills the disk over a few hundred resets. It now has its own test asserting the dangling-volume count does not grow, and that test does fail under the sabotage. Third time in this epic sabotage found what review did not, and the first time the finding was a comment giving a false reason for correct code. Left open, and already handled elsewhere: **no strategy resets process state** — S-0.5's cached `QuerySet` survives every database-side reset — but S-2.1 destroys the container after every run, so the process holding it does not survive to the next experiment. The reset contract is the database half of a guarantee whose other half is the container lifecycle; a change making containers persistent between runs would silently reopen it.

### S-2.7 — Reset verification harness
Depends: S-2.6
AC:
- Runs seed → workload → reset ten times
- Asserts row counts identical across all cycles
- Checks sequence counters and cache state, not just row counts
- Fails the workload with a clear diagnostic if reset is unreliable, falling back to the next strategy

**DONE (2026-08-06)** — `src/coldfix/sandbox/verification.py`, ADR 026, 21 tests in `tests/sandbox/test_verification.py` against a real Postgres. All four AC met. The fingerprint has **four database parts** — row counts, content hashes, max ids, sequence positions — because S-0.5 proved one is not enough: plain rollback returned identical row counts, content hashes *and* `max(id)` across ten cycles while sequences climbed 250. The central test hands the harness that exact defect and requires rejection with the sequence named, asserting alongside it that the other three checks stayed silent — the three S-2.6's criterion asked for are satisfied by the broken strategy, which is the entire point. **AC 3's "cache state" needed a design the obvious one cannot deliver, and this was found by writing the test and watching it pass for a leaking workload.** A workload with a stale cache returns the same value every cycle; so does a correct one, because a correct reset makes every cycle identical — the two are **indistinguishable by output**. So the observation comparison stays (it catches state the fingerprint cannot reach, like a table it cannot hash or a file) but is *not* the cache check. Cache state is checked by requiring **`process_identity` to differ every cycle**: a process that survives can carry a cached row no reset will clear, which is the *condition* for the defect rather than the defect, and is checkable without knowing the framework. That turns ADR 025's claim — S-2.1 destroys the container after every run — from an assumption into something checked, and would notice if containers were ever made persistent as an optimisation. Supplying no `process_identity` skips it, documented as a real hole at every level. The content hash **orders rows by their own text**, without which a restore's changed physical order makes every correct strategy look broken. An unreliable reset is a **report, never an exception** — `SNAPSHOT_RESTORE` exists because rollback cannot undo another connection's commit, so failure is expected traffic; only running out of candidates raises, carrying every report. `VerifiedReset` cannot be built from a failing report, which is the third use of that construction after `VerifiedDatabase` and the session types, and is what makes S-2.6's "verified before use" unskippable. **Sabotage-verified on five properties, each asserting the edit applied** — a precaution added after ADR 024, where two sabotages silently no-opped: row-counts-only fails 3, dropping the observation comparison fails 1, dropping the process check fails 1, `choose_reset` returning its first candidate regardless fails 2, removing the content hash's `ORDER BY` fails 1. Left open: verification is evidence about **the workload it ran**, and a reset that returns the state after one workload may not after another — S-0.5's file-writing workload is reset by no strategy here, and S-7.x's workload artifacts are where that pairing gets recorded.

### S-2.8 — Real-time system detection and refusal — **SAFETY**
Depends: S-0.1
Why: measurement-based analysis is provably insufficient for WCET, and a caching optimization would improve every metric we measure while degrading worst-case timing.
AC:
- Detects RTOS imports, deadline annotations, safety-certification markers, and known real-time framework signatures
- On detection, refuses to proceed and explains why in one paragraph
- Detection runs before grounding, not after
- A test fixture with real-time markers is refused

Notes: this is the only category where running the system could make things worse while reporting success. Ship it before the tool is ever pointed at an unfamiliar repository.

**DONE (2026-08-06)** — `src/coldfix/sandbox/realtime.py`, ADR 027, 48 tests in `tests/sandbox/test_realtime.py`, fixture pair in `tests/fixtures/realtime/`. All four AC met. **Detecting real-time systems is easy; not refusing Django applications is the whole problem**, and that inversion is what the story turned out to be about. This tool's pinned development target is a helpdesk; `deadline` is an ordinary field name in half the task trackers ever written, and `scheduler`, `priority`, `critical`, `real-time` and `safety` are ordinary English appearing in exactly the software this system exists to speed up. A detector keying on those refuses its own target on day one and is worse than no detector, failing in the direction that makes the tool useless while looking diligent. So **every pattern is anchored to a token that does not occur in ordinary application code** — `SCHED_DEADLINE` not `deadline`, `IEC 61508` not `safety`, `\bSIL[- ]?[1-4]\b` not `SIL`, the last because a bare `SIL` matches *silicon*, *silent* and `SILENCE_DEPRECATION`. **The fixture is a pair and the control is the load-bearing half** (ADR 006): `flight_controller` plants markers in all four categories and is refused; `task_tracker` is an ordinary web app packed with every tempting word at once and must be **cleared**; a third test asserts the control still *contains* those words, because the way that claim stops meaning anything is somebody tidying the vocabulary out of the fixture rather than the detector changing. **AC 3 is structural**: grounding will require a `ScreenedRepository` and screening is the only thing that makes one, so there is no unscreened repository object for grounding to accept — fourth use of that construction after `VerifiedDatabase`, the session types and `VerifiedReset`. **An incomplete scan is not a clear one**: a repository too large to finish is refused certification rather than reported clean, because for a check whose failure mode is degrading a safety-critical system while reporting success, "nothing found" and "we stopped looking" must never be the same answer. `screen()` reports and `ScreenedRepository` decides, and the refusal names the marker, category, file and line, since a refusal nobody can audit is one that gets worked around. Generated trees (`node_modules`, `.venv`) are skipped; `vendor` and `third_party` deliberately are not, because a vendored RTOS is exactly what is being looked for and third-party code being unpatchable does not make it undetectable. **Sabotage-verified on five properties, each asserting the edit applied**: loosening the deadline pattern to a bare `\bdeadline\b` fails 4 tests including the control — the most valuable result here, because it is the mistake that would have shipped — matching `SIL` without its number fails 3, treating a truncated scan as clear fails 1, screening without refusing fails 3, scanning binary files fails 1. Left open: a system whose timing requirements live only in a specification document is not detected, since the screening is evidence-based. And **this repository would refuse itself** — `realtime.py` holds every pattern as a literal and the fixtures plant markers on purpose — which is correct behaviour rather than a bug, and is why the self-check test is pointed at `src/coldfix/bench`; recorded so nobody "fixes" it with a self-exemption any repository could use by naming a directory `coldfix`.

### S-2.9 — Scope refusals
Depends: S-2.8
AC:
- Concurrency and locking findings are marked `diagnose-only` and can never enter the repair path
- Causes localized inside third-party dependencies are reported, never patched
- Unsupported project types (frontend, mobile, embedded, mainframe) are detected where possible and reported honestly

**DONE (2026-08-06)** — `src/coldfix/sandbox/scope.py`, ADR 028, 52 tests in `tests/sandbox/test_scope.py`. All three AC met. **The three bullets are not one thing, and reading them as one invites one implementation.** `00-BRIEF.md` §3 already draws the line: a *refused on principle* table of four categories where no verifier makes a change safe, and a separate *not covered* list which is a capability boundary. Concurrency and third-party are the first kind and are enforced **structurally, per finding** — the repair path takes a `RepairableFinding` and constructing one runs the classification, so a diagnose-only finding has no route to repair rather than a rejected one (fifth use of that construction, after `VerifiedDatabase`, the session types, `VerifiedReset` and `ScreenedRepository`). Unsupported project types are the second kind and are **reported, not refused**: `report_scope` returns rather than raising, because a Django application with a React frontend is a perfectly good subject *for its backend* and refusing the repository would decline work this system can do. A test asserts that direction specifically, since collapsing it into a refusal is the obvious tidy-up and would quietly cost the tool most of its real subjects. **`classify()` takes a mechanism string and a site path rather than a finding object**, because the evidence-chain schema belongs to E8 and inventing one here would have the Diagnostician inherit a shape it did not choose; when that schema arrives it supplies these two fields and nothing changes. **Erring toward `DIAGNOSE_ONLY` is deliberate and is the opposite of S-2.8's bias** — a wrongly-refused fix costs one fix, a wrongly-permitted concurrency patch risks a race no check here can detect — so *deadlock-free* is treated as a concurrency mention rather than a control, an algorithm chosen for deadlock-freedom being a concurrency change. **Sites are POSIX regardless of host**: a stack frame comes from a Linux container and `Path("/usr/lib/...").is_absolute()` is `False` on Windows, so relying on it would report every standard-library site as the user's own code in development and as third-party in CI — found by a test failing on Windows, and invisible on the platform the system actually runs on. Third-party trees are excluded from project-type detection and included in S-2.8's marker scan, which is not a contradiction: React in `node_modules` is something the project *uses*, a vendored RTOS in `third_party` is something the project *is*. **Sabotage-verified on five properties, each asserting the edit applied**: a substring search for `lock` fails 5 tests on *clock*, *unblocking* and *block*; classifying without blocking repair fails 3; treating a bare `package.json` as a frontend fails the control (the Django-with-Tailwind case, which is most of the target population); searching inside `node_modules` for project type fails 1; raising instead of reporting for unsupported areas fails 1. Left open: the repair path does not exist yet — E10 owns the Surgeon — so what exists is the guarantee that it will have nothing but `RepairableFinding` to accept, the same qualification as ADR 024's "refuses to start"; and area detection is by manifest and extension, so an undeclared out-of-scope part is not detected, which is why the AC says *where possible*.


**EPIC 2 COMPOSITION CHECK (2026-08-06)** — after S-2.9, the epic was run as a whole for the first time, and **it could not perform its own purpose**. Nine stories of module tests, five constructor-enforced safety properties, four sabotage findings and 487 passing tests, and no test anywhere had a containerised workload reach a database: the reset tests connected to Postgres from the host, the sandbox tests ran workloads that talked to nothing, and the two sets of files had zero overlap. Writing the end-to-end test was **impossible as the code stood** — `Sandbox` hardcoded `--network none`, giving a container loopback and nothing else, so the architecture as built refused to run a Django application. `docker_run_argv` had already named the answer in a comment and filed it as standup's problem; a story that never ran a real workload never noticed it was its own. Fixed in ADR 029: `Sandbox` gains a `network` field typed `InternalNetwork | None`, where `InternalNetwork`'s constructor runs `docker network inspect` and refuses anything not reporting `Internal: true` — so AC 3 is kept rather than traded, since a container on such a network cannot reach `1.1.1.1` and can query a sibling database, measured before the code was written. A second problem appeared only once the first was fixed: `SnapshotRestoreReset` connects from the *host*, which cannot reach an internal network, so the database sits on **two** networks (default bridge with a published port for the harness, internal network under the alias `db` for the workload) while the workload container sits on one. The subject's code has no route off the host; the database, which runs no subject code, is reachable by the thing that must reset it. S-2.1's brittle field-set test **failed when the field was added**, which is what it was written for, and now also asserts the annotation so a future change to `str` fails the same way. `tests/sandbox/test_end_to_end.py` runs ten cycles of sandboxed workload and reset through the S-2.7 harness and asserts every cycle ran in its own container. **The lesson: per-module verification says nothing about composition, and a suite where every file tests one import will not tell you.**

---

# EPIC 3 — Primitives

**Goal:** fourteen experiment types the Diagnostician can compose.

**Why the ordering:** ablation and scaling first because they carry the most weight; the rest as they become needed.

### S-3.1 — Primitive registry
Depends: S-0.1
AC:
- Every primitive declares: name, required capabilities, cost class, applicability predicate
- The Diagnostician receives only primitives applicable to the current project
- Adding a primitive requires no change to agent code
- Registry is introspectable for the prompt's instrument list

Notes: this is what makes primitive 15 an afternoon rather than a refactor. Build it before the second primitive, not after the fifth.

**DONE (2026-08-06)** — `src/coldfix/primitives/registry.py`, ADR 030, 34 tests in `tests/primitives/test_registry.py`. All four AC met. **Three of the four declarations are bookkeeping and the fourth is the story.** An applicability predicate typed `Callable[[ProjectProfile], bool]` cannot express the answer this system will have most of the time — *not known yet* — and both flattenings are wrong in ways already paid for: ignorance as `True` runs longitudinal on a CLI tool, which does not fail but fits a flat line, which reads as *no ramp*, which `00-BRIEF.md` §9 ships as an exclusion (ADR 013's failure with a different instrument, and `08-audit.md` F7 is the same shape for proportional perturbation); ignorance as `False` deletes the instrument from the list and the agent concludes it has exhausted the applicable experiments, which is the line `08-audit.md` closes on. So applicability has **four states** — applicable, unsupported here, undetermined, not applicable — chosen because the reader's next action differs for each, and `ProjectProfile.check()` is the only way to read a fact so that `facts.get(fact, False)` is not reachable. **Withholding is recorded, never silent**, and visibility is not callability: `Selection.get()` raises `PrimitiveUnavailableError` for a withheld name carrying its reason, `UnknownPrimitiveError` for one nobody registered. **A selection is a snapshot, and that is ADR 002's requirement rather than a convenience** — tools render at position 0 and prompt caching is a prefix match, so an instrument learned about mid-investigation is available to the *next* investigation, not this one. Capabilities (what this environment provides) and facts (what the subject is) gate independently and are checked separately, because either absence withholds a primitive and the two call for different actions. Cost class is named for its unit — `seconds`/`minutes`/`tens of minutes`/`hours` — because `01-primitives.md` uses "cheapest" for measurement validity in §2 and "most expensive" for wall clock in §5 and §14, and a registry inheriting that ambiguity would hand it to the agent. Signatures are read from the callable with annotations resolved, since a module with `from __future__ import annotations` renders `workload: 'str'` and one without renders `workload: str` — a cached prompt prefix must not depend on an import in a file nobody would connect to prompt cost. **Sabotage-verified on six properties**, each asserting the edit was detected: collapsing undetermined into applicable fails 6 tests, registration-order rendering fails 3, a dispatchable withheld primitive fails 1, a silently overwritten duplicate fails 1, an uncopied facts mapping fails 1, unresolved annotations fail 2. **A seventh sabotage found a hole in the tests instead** — `all_of()` returning the first failing condition rather than the most decisive one passed everything, because the test listed the decisive condition first; it now runs both orders. Like S-1.3, this module ships the mechanism and no primitives. Left open: a fact nobody establishes withholds its instrument, so E7's grounding stories now have a consumer with a stated appetite, and `ProjectFact` grows one member per gate rather than being complete today.

### S-3.2 — Scaling: volume
Depends: S-1.5, S-2.6
AC:
- Runs a workload at three or more scale points with reset between each
- Fits every recorded metric against the scale variable
- Subtracts the framework baseline measured at N=0
- Forces lazy results to materialize before measurement stops
- Clears caches between scale points

Notes: the three failure modes above (baseline offset, lazy evaluation, warm cache) each silently produce a wrong answer. Test each.

**DONE (2026-08-06)** — `src/coldfix/primitives/scaling.py`, ADR 031, 28 tests in `tests/primitives/test_scaling.py`. All five AC met. **The three failure modes are one failure wearing three hats: each flattens a metric that really grows**, and a flat metric is not an error — it is *queries flat across 100x scale*, which §9 of the brief ships as a finding and a human acts on by looking elsewhere. So each is tested in a pair: one test reproduces the wrong answer from the sweep's own numbers, one shows the mechanism producing the right one, because a test that only shows the correct result passes equally against an implementation where the failure was never possible. **The baseline is subtracted always, and the reason it is easy to skip is that it does not change the slope** — it changes the *exponent*, which is what growth classification rests on: S-0.3's netbox floor of ~35 queries against one query per row reads 36,37,38,39 at volumes 1,2,3, exponent 0.05, `CONSTANT`; the same numbers less the baseline are 1,2,3, exponent 1.0, `LINEAR`. A workload that cannot run at N=0 raises rather than skipping the subtraction. **The warm cache is prevented, not detected**, because ADR 026 already proved it cannot be detected — a stale cache and a correct reset both produce identical cycles — so the sweep requires either a process identity that differs at every point or an explicit clear, and **refuses to start given neither**; ADR 026 left that hole open deliberately for verification, and it is closed here because an unqualifiable measurement is worth less than none. Which guarantee was held is recorded on the result, since exclusions carry their preconditions. The measured window closes only after the result is drained, one level deep, and the item count is recorded because a lazy result yielding nothing and a workload returning nothing are the same query count and different findings. Seeding happens **inside** the reset cycle and the reset is a `VerifiedReset` rather than a callable — sixth use of the constructor-enforced construction. **Sabotage-verified on six properties** (dropped subtraction fails 1, dropped materialization 3, no cache-control requirement 1, no identity check 1, fitting only named counters 2, draining strings 1). **A seventh sabotage passed and exposed the test double, not the code**: moving `seed()` outside the reset cycle changed nothing because `RecordingReset` emptied the subject unconditionally, cleaning up after the leak the test existed to catch. A real rollback restores the state as of `begin()`; the double now snapshots and restores, and the sabotage fails with `[0, 0, 1, 2]`. **A test double more forgiving than the real thing turns a structural assertion into a decoration**, and only sabotage finds it. Left open: `seconds` is one sample per point against S-0.4's ~20 ms floor and is labelled `DURATION` for that reason — interleaved timing is S-1.6 and instruction counting S-3.19; the eight parameters stay flat rather than becoming the workload artifact S-4.1 owns.

### S-3.3 — Scaling: shape
Depends: S-3.2
Why: uniform fixtures hide skew-dependent defects at every volume.
AC:
- Generates fixtures with configurable distribution: uniform, power-law, long-tail
- Can hold volume constant while varying distribution
- Distribution used is recorded in every measurement
- A test proves a skew-dependent defect is invisible under uniform data and visible under power-law

Notes: an N+1 that costs milliseconds at three related rows and minutes at three thousand is invisible if every generated parent has exactly three children.

**DONE (2026-08-06)** — `compare_shapes` and `allocate` in `src/coldfix/primitives/scaling.py`, the skew fixture in `tests/fixtures/planted/skew.py`, ADR 032, 38 tests in `tests/primitives/test_scaling_shape.py`. All four AC met. **The blindness is provable, not anecdotal**: a per-parent cost totals `Σ k²` up to constants, and `Σ k²` is minimized exactly when every parent holds the same count (Cauchy-Schwarz), so the uniform fixture is the *provably weakest* shape for that whole class — and it is what `build_store(authors, books_per_author)`, this project's own fixture generator, produces. **The allocation is generated here so the volume cannot move with the shape**: every distribution returns exactly `groups` counts summing to exactly `total`, by largest-remainder apportionment, because rounding each share independently silently gives 199 rows under one shape and 201 under another and a comparison where both moved attributes nothing. Every parent gets at least one child — a shape that empties parents varies the parent count too. No RNG anywhere, so the same arguments give the same fixture on every machine, which is what S-5.1's replay cache will key on. **The risk this story is most likely to be failed on is shipping three names for one distribution**, and it nearly was: the first long-tail definition gave a head mass of 0.37 against the power law's 0.39. It is now the shape data engineers mean — *most customers have one order, one has fifty thousand* — bimodal rather than smooth, and the deliberate worst case the note describes. A test separates all three on mass concentration *and* spectrum. The baseline costs something different on this axis: a volume sweep loses its exponent, a shape comparison loses its **ratio** — a fixed floor of 2,000 comparisons drags a 9.1x sensitivity down to 3.5x, always toward looking survivable. `sensitivity` returns infinity when the reference shape charged nothing (the literal case of *invisible*, e.g. a chunked fetch below its threshold) and 1.0 for zero against zero, because a metric nothing spends is not a finding. **`scale_volume` gained a required `distribution` argument in this story** — AC 3 says *every* measurement, and a growth curve measured under uniform data is exactly the blind result; declared by the caller, like `time()`'s `fresh_process_per_sample`, since the function cannot see what a seeding callable generated. **Sabotage-verified on three properties** (independent rounding fails 12, long tail defined as a power law fails 3, no one-child floor fails 23). **Two assertions in this story were wrong before they were run, both assuming a shape was more extreme than it was**; the second is what exposed the duplicate distribution, and slightly lower thresholds would have shipped it. Left open: a subject whose seeding cannot take a per-parent allocation gets the volume axis only.

### S-3.4 — Ablation with record-and-replay stubs
Depends: S-2.3, S-0.4
AC:
- Records a real return value for the target during a baseline run
- Replays that value during ablation
- Falls back to a minimal valid value where replay is impossible (streams, stateful objects)
- **Records which strategy was used** in the experiment result
- Runs only in diagnostic mode — enforced, not conventional

Notes: stub choice changes what is measured. An empty-collection stub measures the component *plus* all downstream work that consumed its output; a replayed real value measures the component alone. The interpretation differs, so the strategy must be recorded.

**DONE (2026-08-06)** — `src/coldfix/primitives/ablation.py`, ADR 033, 26 tests in `tests/primitives/test_ablation.py`. All five AC met. **S-0.4 handed this story three findings and each became a mechanism.** The replayed value is the one whose size is **closest to the median** of everything observed, and the distribution it came from is recorded beside it: the obvious implementation — keep the first value seen — is exactly what the spike did on its first run, recording a one-followup value out of a population whose median was six, which silently converts the replay strategy into the empty one. *A replay stub that is not size-representative measures the component plus most of the downstream work, under a label saying it measures the component alone.* The **cardinality gap** is computed rather than assumed small (the spike measured +0.8% and said it would not stay harmless if the component fed something expensive — the ablated run is then charged more downstream work than the baseline ever did and the delta **understates** the component). A **single-use iterator is passed through untouched** and selects the fallback, because capturing a generator means consuming it and the workload that asked for it then receives nothing — a baseline measuring a workload that did no work. Recorded values are **deep-copied once at record time**, charged to the baseline alone, so the delta can only be conservative; the stub returns the same object to every call because copying per call would charge that cost to the ablated condition only, which is the distortion S-0.4 avoided with a module-level flag. Where neither strategy is available the ablation is **refused** — a `None` stub measures how long the workload takes to raise `AttributeError`, which looks exactly like a very fast component. AC 5 is enforced by **requiring a `DiagnosticSession` object rather than a mode flag**: it can only come from `Workbench.open(mode=DIAGNOSTIC)`, has no method returning a diff (ADR 022), and a `CandidateSession` is a sibling type so passing one fails type-checking and is refused at runtime. Shared cycle machinery moved to `primitives/measurement.py` — the second caller, which is the point `CLAUDE.md` permits an abstraction. **Sabotage-verified on four properties** (first-value replay fails 3, no mode check fails 2, consuming the iterator fails 3, aliasing the recorded value fails 3). **The last sabotage initially found only 2, and the missing one was a weak test**: it asserted the recorded *size*, which is computed eagerly and survives aliasing, rather than the recorded *value*, which is what gets replayed — an aliased recording of a list the consumer clears replays as empty, this ADR's subject arriving through a third door. Third story running where a passing sabotage found a defective test rather than defective code. Left open: the mode check cannot stop a caller monkeypatching without going through this module, the same qualification ADRs 024 and 028 carry.

### S-3.5 — Delta debugging search over ablation targets
Depends: S-3.4
Why: sequential guessing is O(n); binary search over subsets is O(log n).
AC:
- Implements `ddmin` with a threshold oracle (cost exceeds X) rather than a boolean crash oracle
- Implements the `dd` variant that isolates the difference between a fast and a slow case
- Localizing among 40 candidates completes in materially fewer than 40 ablations on a synthetic test
- Handles the case where removing a subset breaks the workload entirely

Notes: prefer `dd` over `ddmin` — we usually have both a fast case and a slow case, and isolating the difference is exactly what `dd` does.

**DONE (2026-08-06)** — `src/coldfix/primitives/search.py`, ADR 034, 23 tests in `tests/primitives/test_search.py`. All four AC met. **Measured: 40 candidates localize in 11 ablations** for both algorithms (`dd` asks 15 questions and the cache turns 4 into no run), against 40 for one-at-a-time. **`UNRESOLVED` carries the whole adaptation and does two jobs.** The first is AC 4 — a subset that breaks the workload has its exception recorded against the configuration that caused it and the search carries on, a measured failure to measure. The second is not in the story and matters as much: **a measurement near the threshold is a coin flip**, and S-0.4's ~20 ms noise floor is wide enough to decide a branch and so change which component gets named. The oracle takes a `resolution` and answers `UNRESOLVED` inside that band rather than guessing — the state the algorithm already has, used for the thing it is for; zero for counts, which are exact. **The 1-minimality guarantee is weakened and says so**: delta debugging assumes monotonicity, and a component that populates a cache another reads makes the second cheaper by being present, so what is returned is 1-minimal *as measured*. **Both ends are checked before the search runs**, two measurements, each failure a finding: everything active not expensive means the cost is not here (an exclusion), everything ablated still expensive means no candidate owns it — which is exactly S-0.4's shape, where ablating the dominant component left 504 queries of a second N+1 underneath. Without the checks a search completes and names an arbitrary innocent subset with full confidence. Stubs are recorded **once, before the search**, since re-recording per configuration would vary more than the thing being varied. A target never called is refused rather than skipped. **Sabotage-verified on five properties** (no cache fails 1, no resolution band fails 1, narrowed exception handling fails 3, each precondition fails 1). **The cache sabotage found a defect in the instrumentation, not the code**: `measurements` was derived from distinct configurations seen, so disabling the cache doubled the real ablations while the reported number stayed identical — and that number is what AC 3 is judged on. It now counts calls into the workload. Fourth story running where sabotage found what review had not, second where it was in the measuring rather than the measured.

### S-3.6 — Observation: on-CPU counters
Depends: S-1.3
AC:
- Counters for: database queries, rows returned, bytes returned, HTTP requests, file opens, allocations
- Each counter attaches via a framework-specific hook declared by the adapter
- Stack capture per event, optional
- Counter overhead verified under 5%

**DONE (2026-08-07)** — `src/coldfix/primitives/counters.py`, ADR 035, 26 tests in `tests/primitives/test_counters.py`. All four AC met, one of them only after restating it. **Four of the six counters need a magnitude, so `Record` grew one** — a protocol whose amount defaults to 1, and `Count` carries `events` and `total`. One mechanism rather than two, because `db.query` and `db.rows` are the project's canonical guard pair and both numbers must come from the same attachment: a second wrapper would double the cost on the hottest path and let the two drift apart. `measure_once` now records both for every hook, since `db.rows` read as events is the query count — a plausible number and the wrong one. **Most of the module is a vocabulary and that is the deliverable**: ADR 013 made an unknown hook name raise, which only helps if there is one spelling to be wrong about, so the catalogue is the spelling and a name outside it is refused *at registration* rather than when some primitive asks for the counter nobody registered. **Allocations do not fit the hook shape and are not forced into it** — nothing in Python fires per allocation without a C-level profiler, so it is declared a `BLOCK_METER` with tracemalloc's own tracebacks for attribution; the alternative was inventing events, and an invented event is a fabricated measurement. **AC 4 turned out to be unstatable as written, and that is the finding.** The counter measured **77% overhead** against this suite's stand-in cursor — and the counter was fine: its cost is a fixed 0.49µs per event, and the cursor takes 0.4µs. *Under five percent* is a property of a **pair**, not of an instrument, so the denominator is now stated: 366µs, ADR 013's measured instrumented database call. Against that, counting costs **0.13%**, plus an absolute bound of 5µs/event because the defect ADR 013 records cost 590µs and would pass any ratio stated against a slow enough operation. **Per-event stack capture has no fixed percentage at all**: it walks the whole stack, so cost is linear in depth — measured 12.4µs at depth 0, 25.9 at 10, 86.5 at 50, 295.7 at 200, about 1.4µs a frame, while plain counting stays flat at ~0.4µs. A Django request is tens of frames deep before the view, so at a realistic depth **a stack per event costs as much as the database call it observes** (86µs against 366µs is 24%, five times the budget). **S-3.9 inherits this** — sample events or bound the walk, but a screening sweep must not turn it on. tracemalloc measured **327%** of the run it observes and is declared `HEAVY` for it. **Sabotage-verified on four properties** (bypassing the catalogue check fails 1, discarding the recorded amount fails 2, resolving the measuring hook's target at construction rather than install fails 1, leaving tracemalloc running fails 2). The resolve-at-install rule is written against a failure this codebase produces: S-3.4 replaces attributes with ablation stubs, so a hook capturing its target at construction would measure a callable nobody is calling.

### S-3.7 — Observation: off-CPU
Depends: S-3.6
Why: without it the entire saturation column of the USE Method is unmeasurable.
AC:
- Measures time blocked on: disk I/O, network, lock acquisition, scheduler queueing
- Distinguishes "computed a lot" from "waited a lot" in the experiment result
- Works inside the container sandbox
- A test with a deliberate sleep shows blocked time, not CPU time

Notes: an ablation without this tells you a component is expensive but never whether it computed or waited — and those have nothing in common as fixes.

**DONE (2026-08-07)** — `src/coldfix/primitives/off_cpu.py`, ADR 036, 20 tests in `tests/primitives/test_off_cpu.py`. All four AC met. **The total is one subtraction and it is exact**: `perf_counter` is elapsed, `process_time` is CPU charged to the process, and the difference is time the process existed and was not running — no sampling, no tracer, two clock reads. That cheapness is why it is recorded on **every** measurement (AC 2) rather than only when off-CPU time is already the hypothesis. **Attribution by category is a different problem and the honest answer is partial**: the real blocking calls are not reachable from Python — `io.BufferedReader.read`, `socket.socket.recv` and `_thread.LockType.acquire` are C types whose attributes cannot be replaced — so attribution comes from what an adapter *declares* (`blocking()` wraps a known waiting point and records elapsed seconds, reusing S-3.6's magnitude record so blocked time is read by every primitive that reads any counter) and from what the OS already counted (`getrusage` voluntary/involuntary switches, block I/O). **Scheduler queueing has no hook and never will** — being preempted is not a call anything can wrap — so `blocking()` *raises* for that category rather than accepting a wrapper an adapter would then believe had instrumented queueing; it comes from involuntary context switches. **Unavailable is `None`, never `0`** (ADR 013's rule in its original form): zero involuntary switches is a publishable finding — *nothing was preempted, so the cost is not queueing* — so a platform that cannot measure must not be able to produce it, and `resource` does not exist on Windows. **Negative blocked time is reported, not clamped**: CPU above wall clock means the work ran on more than one core, which is `Boundedness.PARALLEL` and needs S-3.12, not a subtraction — clamping would print *never waited* from a case where the decomposition does not apply. **AC 3 was run rather than argued**: the host is Windows, where `resource` does not exist at all, so the signals this story leans on are exactly the ones unverifiable locally; a docker test writes a probe into the sandbox and asserts real blocked time, CPU time and switch counts from inside it. **Sabotage-verified on four properties** (unavailable-as-zero fails 1, clamping fails 1, recording outside a `finally` fails 1 — a call that times out has waited exactly as long as the timeout and that is the finding — dropping the metrics from `measure_once` fails 1). **One test was flaky rather than sabotage-sensitive and was replaced**: four Python threads busy-looping cannot reliably produce CPU above wall clock because the GIL keeps them on one core, and the skip guard did not match the classification threshold, so between the two it tested the scheduler rather than the classification. The parallel case is now constructed directly and the threaded test uses `sleep`, which does release the GIL.

### S-3.8 — Guard counters and global resource envelope
Depends: S-3.6
Why: guard pairs are a denylist and fail by omission.
AC:
- Every primary counter has a declared guard counter
- **Additionally**, every candidate is measured against a global envelope: peak RSS, total CPU, wall time, bytes written, file descriptors, process count
- Any envelope metric outside tolerance triggers a flag regardless of whether that trade was predicted
- A test proves a patch trading queries for a memory explosion is flagged

**DONE (2026-08-07)** — `src/coldfix/primitives/envelope.py`, guard resolution in `counters.py`, ADR 037, 23 tests in `tests/primitives/test_envelope.py`. All four AC met. **AC 1 exposed two guards S-3.6 had left as prose** (`"http.request, by response size"`) **and three as `None`** — guards that guard nothing while looking like guards. `guard` is now a required reference that must name another catalogue counter or an envelope metric, checked **at import**, so a dangling guard fails the import rather than the investigation; six catalogue entries were added to give the references somewhere to point (`http.bytes`, `memory.bytes`, three `blocked.*.calls`), each the other reading of a hook that already existed. A guard may point at an **envelope metric** — `file.open` has no counter to be traded against, and inventing one to satisfy a rule would be worse than admitting it. **The envelope checks every metric always and has no argument for expectations**: `compare` takes two samples and a tolerance table, and there is no parameter through which a caller could name the trade to expect, which is the entire difference from a guard pair. Increases flag, decreases never do. **The finding of the story is that a rise must clear a ratio *and* an absolute floor**, and it arrived as the check failing its own control — two identical runs, 2.4ms and 2.7ms, an 11% rise past a 10% tolerance, nothing having happened. The timing floor is not a guess: it is **S-0.4's measured ~20ms noise floor**. Counts get floors at the other end (two file descriptors becoming three is a 50% rise and is nothing). **Retained memory is a difference, peak RSS is a level**: `sys.getallocatedblocks()` is interpreter-wide, so under pytest a run retaining 24,000 blocks against a 200,000-block interpreter reads as a 12% rise and *passes* — differenced, it is what that block retained, which is what a cache is. Unavailable metrics are **named, not passed**; the report says outright that it covers less than a sandbox run would. **Sabotage-verified on five properties** (unmeasured-as-checked fails 2, checking a chosen few fails 10, no absolute floor fails 2, flagging decreases fails 1, disabled import check fails 1). Two of this story's own tests failed before the code did and both were right to: the first candidate "patch" did not actually reduce the query count, so AC 4 was asserting an improvement that was not there.

### S-3.9 — Stack localization
Depends: S-3.6
AC:
- Normalizes stacks by stripping framework-internal frames from an adapter-supplied deny list
- Groups events by normalized signature
- Walks to the divergence point — the deepest frame common to all occurrences
- Emits the causal site plus the dependency closure (models, relationship declarations, consumers, callers)
- Handles async boundaries where context is lost, or reports that it cannot

Notes: this is how findings span multiple files without the agent reading the repository. The runtime names the files.

**DONE (2026-08-07)** — `src/coldfix/primitives/localization.py`, ADR 038, 23 tests in `tests/primitives/test_localization.py`. All five AC met. **Stripping happens everywhere in the stack, not at the ends** — a real stack is framework, subject, framework, server, and stripping only the innermost run leaves forty frames of framework in every signature and groups nothing. **The divergence point is the longest common suffix and its first element is the site**, which is one computation for both shapes: an N+1's stacks are identical so the site is the innermost frame (the line in the loop), and events from two sites share only their caller so the site is the function calling both. The tempting alternative — the innermost frame of the largest group — gets the N+1 right and the second case wrong, which is why the second case has a test. **A sample localizes as well as a census**, because grouping is by distinct route and the walk is over groups: this is the mitigation S-3.6 handed over (stack capture costs ~1.4µs per frame of depth, 86µs an event at realistic framework depth against a 366µs query), and it is safe *by construction* rather than by a caller remembering. **Two things are reported rather than guessed**: occurrences whose every frame is framework have no site in the subject's code and are counted apart (grouping them under an empty signature would invent a shared site — and the fact is a finding, since S-2.9 already routes dependency costs to diagnose-only); and a stack captured inside a coroutine shows the loop rather than whatever awaited, so the group flags that the trail goes cold instead of naming `base_events.py` as the culprit. The **closure is honest about halves**: callers are exact from the runtime, the source excerpt is read by the *harness* (the agent is what must not read the repository), and models/relationships come from an adapter resolver — with none, it says *not resolved*, which is not *there are none*. A closure over nothing is refused, because an empty closure reads as *a site with no dependencies*. **Sabotage-verified on five properties** (innermost-only stripping fails 2, empty-signature grouping fails 2, busiest-group site fails 3, no async detection fails 3, empty closure fails 1). **Two defects were found by the tests before any sabotage**: `Frame` was hashable but not orderable, so the group sort crashed whenever two groups had equal counts — exactly the unrelated-occurrences case — and a Windows-path test used doubled backslashes inside a raw string, asserting about a path it did not mean. Also fixed here: S-3.7's raising-path test was flaky because `process_time` ticks at ~15.6ms on Windows, so `wall - cpu` understates blocked time by up to a tick; the assertions now allow one tick with the number named.

### S-3.10 — Substitution
Depends: S-1.6
AC:
- Swaps an implementation or configuration value and re-measures
- Configuration substitution supports sweeping a range of values
- Every substitution is reversible and reverted after measurement
- Query-plan comparison supported for index hypotheses

**DONE (2026-08-07)** — `src/coldfix/primitives/substitution.py`, ADR 039, 25 tests in `tests/primitives/test_substitution.py`. All four AC met. **A sweep returns a candidate, not a conclusion**: eight values measured once each cannot separate differences below S-0.4's ~20ms floor, so `confirm` is what turns the candidate into a claim by putting it against the incumbent through S-1.6's interleaved comparison — the same *search first, then validate the single winner* pattern `01-primitives.md` §12 states for instruction counting. The sweep's own explanation says it is a search result and repeats §9's warning that a value tuned on one workload is a claim about that workload only. A sweep that recovers the incumbent proposes nothing and `confirm` refuses it, which is a real result. **Reverting is verified, not performed** — restore in a `finally`, then read the value back and check it. A restore that appears to work and does not is not hypothetical: an object with its own `__setattr__`, a cached property or a validating settings class accepts the call and keeps its own value, raising nothing and silently changing every later measurement. An unreadable original is refused **before anything changes**, and settability is checked by attempting the real assignment rather than probing with a test write, since a probe is a mutation taken before the caller asked for one. **Query plans are the planner's opinion and are labelled as such**: `EXPLAIN` without `ANALYZE` measures nothing, so a comparison reports the *shape change* (sequential scan became index scan) as a fact and the costs as an estimate, saying outright that the workload still has to be timed or counted; `analyze=True` exists and is not the default because an `EXPLAIN ANALYZE` of an `INSERT` inserts. An index the planner ignores is a reported outcome, and nested plan nodes are all read (an index scan under a sort is still an index scan). **Sabotage-verified on five properties** (no restoration check fails 1, restore outside the `finally` fails 1, outermost-node-only fails 1, sweep not restoring between readings fails 2, no-op confirmation allowed fails 1). **The first sabotage silently did not apply and reported five clean passes** — the formatter had reflowed the target text, so the replacement matched nothing and the suite ran against unmodified code. That is ADR 024's failure, and every run above now asserts the edit landed. Left open: pool sizes and cache TTL are in §9's target list *and* in `00-BRIEF.md` §4's slack-reducing list, so a sweep can recommend exactly what the metastability gate exists to catch — S-10.6 owns that classification, and the sweep carries the attribute and value it changed so it can.

### S-3.11 — Temporal
Depends: S-2.2, S-1.6
AC:
- Checks out an earlier revision in a worktree and runs the same workload
- Bisects across a commit range to find a regression point
- Handles revisions that fail to build by reporting and skipping
- Verifies the workload exists at both endpoints before starting

**DONE (2026-08-07)** — `src/coldfix/primitives/temporal.py`, ADR 040, 17 tests in `tests/primitives/test_temporal.py` (real git, real worktrees, marked `slow` at ~80s). All four AC met. **`Oracle` was generalized rather than duplicated**: S-3.5 already built a threshold oracle with a noise band, an outcome cache and an append-only probe log, and a bisect needs exactly that keyed on a revision — two oracles with separately-invented noise semantics would produce findings that disagree for reasons nobody could see. **A revision that cannot be measured is skipped, never counted as cheap**: counting it cheap moves the boundary past it and yields a confident wrong commit, counting it expensive does the same in the other direction. Candidates are tried nearest the midpoint first, since the closer a usable revision is to the middle the less a skip costs. A range where everything between the ends is unmeasurable **reports the pair** — a smaller answer than a commit and a far better one than the wrong commit — naming every skipped revision. The noise band is a skip too: a revision inside the resolution decides a step on noise and every later step inherits it. **Both endpoints are measured first**, with three distinct messages: the older end unmeasurable is usually *the workload did not exist yet*, and a bisect over that returns the commit that **added the workload** — true, and not the regression (AC 4); the older end already expensive means the regression predates the range (an exclusion); the newer end not expensive means there is no regression here (also a result). Worktrees are destroyed in a `finally`, because the diff from an old revision to the current one is a revert of everything since. **Sabotage-verified on four properties** (unmeasurable-as-cheap fails 3, no endpoint check fails 3, destroy outside `finally` fails 1, bisect narrowing the wrong way fails 3). Two test defects were found before any sabotage: the history fixture wrote identical costs in consecutive commits and git refuses a commit that changes nothing, so every test errored in setup with an empty stderr; and the noise-band test poisoned its own endpoint. **A scripted `str.replace` silently matched nothing for the second story running** — this time applying one edit of two and leaving the other, which read as a fixture change that had not happened. Edits to existing files now go through a tool that fails when its target is absent.

### S-3.12 — Load with USL fitting
Depends: S-1.5
AC:
- Drives concurrent load at increasing levels at fixed data size
- Fits throughput against concurrency, returning contention (α), coherency (β), and Nmax
- Cross-checks measurements against Little's Law for self-consistency
- Findings are marked `diagnose-only`

Notes: the fitted coefficients are diagnostic, not just descriptive — high α points at a shared resource, high β at coordination cost. Surface them to the agent, not just the curve.

**DONE (2026-08-07)** — `src/coldfix/primitives/load.py`, ADR 041, 25 tests in `tests/primitives/test_load.py`. All four AC met. **The fit is ordinary least squares because the model linearizes**: `(γN/X(N) - 1)/(N-1) = α + βN`, intercept is contention and slope is coherency, so ADR 015's stdlib-only rule holds. **Two guards were added because the tests demanded them, and both are the same lesson — a fit always returns numbers.** (1) *A sign test on a fitted coefficient needs a tolerance*: a curve generated with β=0 fits β=-8.6e-08 and one with α=0 fits α=-1.2e-06, neither of which is negative contention, and a strict `>= 0` declares an ordinary Amdahl-shaped system unfittable. The floor is **1e-3, chosen against what a load measurement can resolve** — α=0.001 is one part in a thousand serialized and no timing-based test separates that from zero. Same rule S-3.8 needed for its envelope, at the other end of the scale. (2) *A peak beyond the measured range is an extrapolation*: β is never exactly zero and `sqrt((1-α)/β)` turns a tiny β into an enormous peak — measured, a β=0 curve rounded to whole completions gave β=6.5e-5 and a confident peak at N=118 from data stopping at 16. `Nmax` is withheld past twice the largest concurrency driven. A materially negative coefficient is **reported as measured** with the fit marked unfitted; replacing it with zero hides the finding behind a curve that looks fitted. **Little's Law is a validity check, not a result** — `N = X × R` costs one multiplication and catches the failure nothing else sees: a generator that never sustained its concurrency still produces a smooth, plausible, meaningless fit, so the finding says to fix that *before* reading the coefficients. **Diagnose-only is enforced twice** and the second is the real one: the mechanism sentence is written so S-2.9's `RepairableFinding` refuses it in its constructor, independently of what this module remembers about itself. The GIL is stated rather than hidden — for CPU-bound Python the pool is not really concurrent, and Little's Law is exactly what notices. **Sabotage-verified on four properties** (negative coefficients accepted fails 2, no extrapolation limit fails 1, Little's Law always agreeing fails 2, finding marked repairable fails 2). **The extrapolation sabotage initially passed**, showing the branch was unreachable from any test — every curve had either a real peak in range or a β below the floor. A test for the case the rule exists for was added, with its control. A guard no test reaches is a guard nobody has checked.

### S-3.13 — Isolation
Depends: S-3.12
AC:
- Runs a component standalone and in full context, reporting the gap
- Findings marked `diagnose-only`

**DONE (2026-08-07)** — `src/coldfix/primitives/isolation.py`, ADR 042, 21 tests in `tests/primitives/test_isolation.py`. Both AC met. **A gap smaller than the spread of the isolated runs is not interference** — two runs of the same thing differ, so a primitive reporting any positive gap reports interference for everything it is pointed at, and what makes that worse than an ordinary false positive is that **the finding names a real neighbour**: a specific wrong answer sends someone to change a queue their component never touched. The isolated condition is run repeatedly, its *range* is the floor (a standard deviation assumes a shape timing distributions do not have, per S-1.5), and a gap inside it is reported as *no interference detectable* — an exclusion, not a failed search. **Attribution is a separate step and is the one §17's `Load → Isolation → Substitution` needs**: a gap against the whole context says a component is interfered with, a gap against each neighbour alone says by what, and where the whole context interferes but no neighbour does, that is reported as the combination rather than one of them being named. The magnitude is a **search result** — same discipline as S-3.10's sweep, confirm with S-1.6 before quoting. Diagnose-only is enforced twice, the second being S-2.9 refusing the mechanism in its own constructor. The context stops in a `finally`. **Sabotage-verified on four properties** (any-positive-gap fails 3, repairable fails 2, a mechanism S-2.9 misses fails 2, context stopped outside `finally` fails 1 — **but only after a test was added**, since nothing raised inside a live context and the branch was unreachable). **The contended test was flaky and the cause is a platform fact**: at a 20ms lock hold it measured 0.0203 alone against 0.0205 contended for a workload holding the lock its whole life, because Windows' timer granularity is ~15.6ms — the same number S-3.9 hit from the other direction. The hold is now 50ms and the file passed four consecutive runs.

### S-3.14 — Proportional perturbation
Depends: S-3.4
AC:
- Injects a known fractional slowdown into a target and measures the effect
- **Applicability predicate returns false for single-threaded synchronous code**
- Returns a sensitivity curve, not a single point

Notes: Coz's virtual speedup works by pausing concurrently running threads. In single-threaded code there is nothing to pause and the primitive degenerates into ablation. Gate it.

**DONE (2026-08-07)** — `src/coldfix/primitives/perturbation.py`, ADR 043, 19 tests in `tests/primitives/test_perturbation.py`. All three AC met. **The gate is not a caveat on this primitive, it is the reason it exists separately from ablation**: in serial code the sensitivity *is* the share of runtime, so the curve reproduces ablation's answer more slowly; in concurrent code the slope is smaller than the share because other threads absorb part of the delay, and that gap is the whole of what Coz's method adds. The primitive declares `RUNS_CONCURRENT_CODE` and S-3.1's registry withholds it where the fact is false **and where nobody has established it** — ADR 030's three-answer applicability doing the job it was built for, on the first primitive that needed it, and tested through `REGISTRY.select` rather than by asserting a docstring. Writing the insensitive-component test made the same point from the other side: **the case cannot be constructed without concurrency**, since the target has to run alongside something longer for its delay to be absorbed rather than added. The slowdown is proportional to what the call took (a constant delay's slope is a fact about the constant); the speedup is an **extrapolation** with r² beside it, the same rule S-3.12 applies to a peak. **The workload must reach the target through the attribute** — a reference captured before the substitution calls the original, the curve comes back flat, and a flat curve reads as *optimizing this would gain nothing*: the wrong answer in the direction of doing nothing. Found by a test passing `pipeline.bulk` directly and measuring a slope of -0.0002 for a component that was the entire workload. Injection reuses S-3.10's verified substitution rather than patching attributes a sixth time. **Sabotage-verified on three properties** (wrong project fact fails 2, constant delay fails 2, any-positive-slope fails 1). **Also fixed here: S-3.13's contention tests flaked in the full suite**, and the cause is a property of locks — measured, the contended samples were `[0.856, 0.050, 0.050, 0.050, 0.050]`, because Python's locks are not fair and a foreground thread that releases and immediately re-acquires barges ahead of the queued neighbours, so five back-to-back measurements contend once. The fixture now does work either side of the critical section (which is also what a real component looks like) and every sample contends; and `context_cost` now states that a median understates contention living in the tail, pointing at S-1.5's rank test for that question.

### S-3.15 — Longitudinal
Depends: S-3.2
AC:
- Runs a workload repeatedly at fixed size over an extended period
- Fits metrics against elapsed time rather than input size
- Applicability predicate requires a long-running deployment model
- Configurable duration with a hard cap

Notes: the most expensive primitive. Never run it on a CLI tool.

### S-3.16 — Fault injection
Depends: S-2.1
AC:
- Degrades a declared dependency: added latency, error responses, dropped connections
- Measures the system's behaviour under degradation
- Blast radius limited to one dependency at a time
- **Detects retry amplification** — outbound call count rising superlinearly under injected latency

Notes: the retry-amplification check is what partially rescues the metastability gate. It does not prove safety, but retries are the most commonly cited metastable trigger.

### S-3.17 — Input space search
Depends: S-3.6
AC:
- Wraps an existing fuzzer (AFL-based) rather than implementing mutation from scratch
- Fitness function rewards resource consumption, not coverage
- Applicability predicate requires user-controlled input parsing
- Findings involving denial-of-service potential are flagged for different disclosure handling
- Hard time cap

Notes: do not write a fuzzer. The agent's contribution is choosing what to fuzz and interpreting results.

### S-3.18 — Bound comparison
Depends: S-3.6
AC:
- Implements only computable bounds: bytes that must be read, rows required by the response schema, instruction lower bounds
- Returns "not computable" for semantic bounds rather than guessing
- Used during screening as an optional headroom check

Notes: "how many queries must this endpoint issue" is a question about intent and is circular. Restrict to the computable cases and say so.

### S-3.19 — Observation: deterministic instruction counting
Depends: S-3.6
Why: **this is the only known way to measure below the timing noise floor**, and `01-primitives.md` §12 already names it while no story implements it.
AC:
- Counts retired instructions for a workload, independent of machine and load
- Counts are reproducible across runs on the same input to within a stated tolerance
- Available to the Diagnostician as a metric alongside wall time and query count
- A test proves two implementations differing by less than the measured timing floor are still separable by instruction count
- The experiment result records which metric the conclusion rests on

Notes: S-0.4 measured the timing noise floor at **~20 ms, about 6% of a 350 ms endpoint**, at 20 repetitions — so a real 2% improvement is invisible to timing no matter how many times it is run. Instruction counts are deterministic, which collapses that floor to roughly zero for CPU-bound work. `01-primitives.md` §12 states the intended workflow: *"search against instruction count, then validate the single winner with proper interleaved statistical timing."* That makes this the enabling primitive for any optimization search (E10 onward), not merely another instrument. It does not help for I/O- or lock-bound cost — that is S-3.7's job, and the two are complementary rather than alternatives.

---

# EPIC 4 — Screening

**Goal:** find what is worth investigating using zero model calls.

### S-4.1 — Workload enumeration interface
Depends: S-0.1
AC:
- A workload exposes: `invoke()`, `scale(n)`, `reset()`, baseline metrics, fixture recipe, reset method
- Defined as a Pydantic model with full validation
- A hand-written workload for the target repo validates against it

### S-4.2 — Growth screening
Depends: S-3.2, S-4.1
AC:
- Measures every workload at two or more scale points
- Computes growth ratio per metric
- Zero model calls — asserted by a test that runs screening with no LLM client configured

### S-4.3 — Flagging and ranking
Depends: S-4.2
AC:
- Flags superlinear growth and unexplained high flat cost
- Ranks by measured magnitude
- **States explicitly that call frequency is unknown** where the project provides no logs or metrics
- Skips healthy workloads

Notes: a 10× win on a monthly batch job outranks a 2× win on the hottest endpoint under magnitude ranking alone. Do not imply a priority the measurements cannot justify.

### S-4.4 — Findings cap
Depends: S-4.3
Why: without it, cost is unbounded.
AC:
- Caps findings investigated per run at a configurable default of 5
- Remaining flagged workloads are listed for the human, not silently dropped
- A test with 30 flagged workloads produces 5 investigations and a list of 25

### S-4.5 — Honest null result
Depends: S-4.3
AC:
- When nothing is flagged, emits a structured null result naming the workloads screened and the thresholds applied
- The null result is a successful terminal state, not an error
- When workloads run but touch almost no data, the message says so specifically rather than reporting "no issues found"

---

# EPIC 5 — Replay cache and cost control

**Goal:** make development fast and production affordable. **Build this before the agents.**

### S-5.1 — Experiment replay cache
Depends: S-3.2
Why: changes iteration speed from ~5 cycles a day to ~50.
AC:
- Cache keyed on `(repo_sha, workload_id, experiment_spec, fixture_hash)`
- Stores the full measurement result
- Cache hit returns in under 100ms
- A previously-run investigation replays end to end with zero model calls and zero container starts

### S-5.2 — Replay mode
Depends: S-5.1
AC:
- A recorded investigation can be replayed to debug downstream agents
- Replay is byte-identical to the original for deterministic experiments
- Non-deterministic experiments are marked and re-run rather than replayed

### S-5.3 — Token accounting
Depends: S-0.2
AC:
- Every model call records: phase, agent, step type, model, input tokens, output tokens, cached tokens, cost
- Cost is queryable per phase, per finding, and per run
- A run report includes euros per confirmed finding

### S-5.4 — Budget enforcement
Depends: S-5.3
AC:
- Per-phase step caps: ground 60, investigate 40 experiments, repair 3 attempts, audit 2 rounds
- Global euro ceiling
- Exhaustion halts, checkpoints, and reports — it does not warn and continue
- A progress check escalates when N steps produce no new information

Notes: caps must be in code, not configuration. The worst case without them is unbounded.

### S-5.5 — Model routing
Depends: S-5.3
Why: ~30 calls per run genuinely need the frontier model; ~220 do not.
AC:
- Each call site declares `creative` or `mechanical`
- Routing maps step class to model tier
- Tier assignment is configurable without code changes
- A test asserts mechanical steps never hit the frontier tier by default

### S-5.6 — Cascade with escalation logging
Depends: S-5.5
AC:
- Cheap model attempted first wherever a deterministic validator exists
- Escalates after two failures
- Escalation rate logged per step type
- **No cascading on hypothesis generation or attack design** — no validator exists for those

### S-5.7 — Cache-friendly context assembly
Depends: S-5.3
Why: the single largest cost variable in the system.
AC:
- Prompt structure is: stable system prefix, stable playbook, stable source, **append-only log**, varying question
- The experiment log is never reordered or re-summarized mid-investigation
- A test asserts the prefix is byte-identical between consecutive calls in one investigation
- Cache hit rate is measured and reported

Notes: 120 calls at 60k uncached costs ~$39; the same calls at 12k with caching cost ~$1.68. Any reordering of the log invalidates the cache and silently multiplies cost.

### S-5.8 — Context pruning with on-demand detail
Depends: S-5.7, S-5.1
AC:
- The log in context holds one-line summaries per experiment
- `read_experiment(n)` returns full output, stacks, and raw counters
- The prompt states explicitly that detail is retrievable
- No information is discarded — only deferred

### S-5.9 — Vendor cost comparison on effective cost
Depends: S-5.3, S-15.3
Why: ADR-002 chose a vendor on SDK ergonomics, not on price, and the question will otherwise be re-argued from list prices and memory.
AC:
- Runs the same fixed scenario set against at least two vendors
- Reports **cost per confirmed finding**, not cost per token
- Reports effective input cost with caching in effect, alongside list price, and states the measured cache hit rate for each vendor
- Reports experiments-to-conclusion per vendor — a dearer model reaching the answer in fewer experiments may be cheaper overall
- Records each vendor's minimum cacheable prefix and cache TTL, since both change effective cost independently of list price
- Result is published in the cost report and supersedes ADR-002's provider choice if the numbers warrant it

Notes: **list price is the wrong number for this workload.** Cache reads bill at roughly 0.1x of input on the first-party API, and the append-only experiment log exists precisely so the cached prefix stays byte-identical — so effective cost is dominated by hit rate, not sticker rate. Caches are also model-scoped, so a vendor that is 30% cheaper per token but has a larger minimum cacheable prefix, a shorter TTL, or weaker prefix semantics can cost more here. S-0.8's harness is already the right shape for this: a fixed scenario set with programmatic scoring, so swapping the request layer is the only work. ADR-002 is a record of a decision, not a commitment — this story exists to make it falsifiable rather than defended.

---

# EPIC 6 — State and persistence

**Goal:** state that survives crashes, and knowledge that survives rewinds.

### S-6.1 — Checkpointed state schema
Depends: S-0.2
AC:
- TypedDict with reducers: `experiments`, `attempts`, `flags` use append semantics
- A test proves a node returning a single experiment appends rather than replaces
- Schema validated on every node transition

Notes: `Annotated[list, add]` is load-bearing. Without it the agent loses its own history, re-tests rejected hypotheses, and loops while appearing to work. This is the most common bug in building these systems.

### S-6.2 — Persistent store
Depends: S-6.1
Why: rewind must not discard the knowledge that motivated it.
AC:
- Separate database holding: failure memory, playbooks, trust ledger, replay cache
- Append-only; no checkpoint restore touches it
- A test rewinds to an earlier checkpoint and asserts failure memory from the later state is still present

### S-6.3 — Store experiment results by reference
Depends: S-6.1, S-5.1
AC:
- Checkpointed state holds hashes and summaries, not full results
- Full results live in the replay cache
- Checkpoint size stays bounded as the experiment log grows
- A 40-experiment investigation produces checkpoints under a stated size limit

### S-6.4 — Post-patch staleness policy
Depends: S-6.1
AC:
- After a patch ships, screening results for workloads touching modified files are invalidated
- Untouched workloads keep their measurements
- Pending findings whose context files were modified are invalidated and re-investigated rather than repaired from a stale chain

---

# EPIC 7 — Explorer agent

**Goal:** turn an unknown repository into a runnable, scalable, resettable workload.

**This is the riskiest component. Build it alone and prove it before anything downstream.**

### S-7.1 — Framework fingerprinting
Depends: S-0.1
AC:
- Detects framework, version, ORM, database, test runner from manifest files and imports
- Returns a structured fingerprint used to key playbooks
- Unknown frameworks produce an honest "unsupported" result

### S-7.2 — Environment standup
Depends: S-2.1, S-7.1
AC:
- Starts the database, installs dependencies, runs migrations
- Distinguishes "database not started" from "database started but rejecting connections"
- `logs(service)` and `ps()` tools available to the agent for diagnosing failures

Notes: the two failure states above look identical without log access. The agent needs those tools or it guesses.

### S-7.3 — Entry point enumeration
Depends: S-7.2
AC:
- Enumerates HTTP routes, CLI entry points, management commands, background job handlers, integration tests
- Returns candidates ranked by likely usefulness
- Handles frameworks where routes are dynamically registered

### S-7.4 — Auth resolution
Depends: S-7.3
AC:
- Detects the auth scheme from settings and failed-request responses
- Creates credentials and attaches them to subsequent requests
- Handles custom user models with non-standard username fields
- Playbook consulted before exploration

### S-7.5 — Fixture discovery
Depends: S-7.2
AC:
- Locates existing factories or fixtures (factory_boy, pytest fixtures, management commands)
- Uses them in preference to synthesis
- Records the fixture recipe in the workload artifact

### S-7.6 — Fixture synthesis
Depends: S-7.5
AC:
- Reads the schema and walks foreign key chains to construct valid rows
- Handles required fields, enums, and unique constraints
- Handles multi-level FK chains discovered on IntegrityError
- Falls back gracefully and reports when synthesis fails

### S-7.7 — Skewed fixture generation
Depends: S-7.6, S-3.3
AC:
- Can generate power-law and long-tail distributions across relationships
- Distribution is a parameter of `scale()`
- Recorded in every measurement taken with those fixtures

### S-7.8 — Objective work verification — **SAFETY**
Depends: S-7.6
Why: the agent is incentivized to claim success because success completes its task.
AC:
- `work_verified` is computed by the harness, not the agent
- Requires all three: query count, response bytes, and wall time each rise between N=10 and N=100 by stated thresholds
- The agent cannot override or supply this value
- A workload failing verification is rejected regardless of what the agent claims

### S-7.9 — Workload artifact emission
Depends: S-7.8, S-4.1
AC:
- Emits a validated workload object
- `evidence_of_work` is mandatory and harness-computed
- Reset method verified by S-2.7 before emission

### S-7.10 — Caps and honest failure
Depends: S-5.4, S-7.11
AC:
- 60-step cap enforced
- Progress check escalates after 15 steps with no new information
- Per-stage attempt budget enforced on top of the global cap (S-7.11 supplies the stages)
- On failure, reports **which stage never completed** and what was attempted there
- **Never reports success when no workload does real work**

Notes: "reports what was attempted" is a transcript; "stage 4 never completed, here is its predicate and the last error" is something a user can act on and S-17.2 can publish. The per-stage budget is the tighter instrument — S-0.3's grounding runs took 5–19 minutes, and detecting at stage 2 that a repo will not ground saves the other seven stages.

### S-7.11 — Stage predicates — **SAFETY**
Depends: S-7.1
Why: without a definition of done per stage, an agent stuck at stage 4 and an agent progressing normally are indistinguishable until the global cap fires.
AC:
- Grounding is modelled as the nine stages in ADR 009, each with a predicate
- **Every predicate is computed by the harness; the agent cannot supply or override one** — the S-7.8 rule, extended to all stages
- Predicates are framework-scoped and resolved through the S-7.1 fingerprint
- A stage whose predicate is already true is complete without action (repos shipping a seeded database skip seeding)
- A test proves an agent claiming a stage complete cannot advance while that stage's predicate is false

Notes: S-0.3 produced sixteen distinct obstacles across three repositories, no two identical, and **every one fell into one of nine stages with no obstacle needing a tenth**. Specifics never repeated; the taxonomy stayed closed. That asymmetry is what makes this tractable — an unfamiliar obstacle inside a known stage is a bounded search against a stated success condition, which is the shape an agent handles. This is the strongest lever found in E0 for the unknown-unknowns problem, and it is what lets S-13.1 key playbook entries to a stage rather than to a whole run.

### S-7.12 — Date-anchored environments
Depends: S-7.2
Why: a repository last touched in 2019 does not break because it is complex; it breaks because we hand it a 2026 toolchain.
AC:
- An anchor date is derived from the repository's most recent commit
- Dependency resolution is constrained to packages published on or before the anchor (`uv --exclude-newer`)
- The interpreter version is read from the repo's own declarations — `python_requires`, classifiers, `tox.ini`, CI matrices — and fetched to match
- The anchor, the resolved dependency set, and any override are all recorded in the workload artifact
- A test grounds a repository whose unpinned dependencies resolve incorrectly at HEAD and correctly at its anchor

Notes: see ADR 010. S-0.3's sample was drawn entirely from repositories committed to within three days of selection, which is why the *Python version mismatch* and *dependency resolution failure* rows of its recurrence matrix came back empty — the spike could not have detected this class. **Anchoring covers the Python layer only**; an old `psycopg2` needing a `libpq` current Debian no longer ships is an OS-level problem with no `--exclude-newer` equivalent, and that residue belongs in S-17.2 rather than being described as solved. The anchor must be overridable, since a contemporary dependency version may carry a since-fixed incompatibility or a known vulnerability.

---

# EPIC 8 — Diagnostician agent

**Goal:** determine the cause by experiment, and switch instruments when a hypothesis fails.

### S-8.1 — Hypothesis generation
Depends: S-3.1, S-5.5
AC:
- Separate call at temperature 0.8
- Receives the experiment log, exclusions, source under suspicion, and applicable instruments
- Returns a structured hypothesis plus the primitive that would test it
- Routed to the frontier tier; no cascading

### S-8.2 — Experiment design
Depends: S-8.1
AC:
- Translates a hypothesis into a concrete experiment specification
- Specification is validated against the chosen primitive's schema
- Mechanical step — routed to the mid tier with cascade

### S-8.3 — Result interpretation
Depends: S-8.2
AC:
- Separate call at temperature 0.0
- Returns verdict: confirmed / narrowed / rejected, with the measurement attached
- A test asserts identical inputs produce identical verdicts across repeated calls

Notes: the split temperature is a real design decision. Hypothesis generation benefits from diversity; interpretation must not vary.

### S-8.4 — Append-only experiment log
Depends: S-6.1, S-5.7
AC:
- Every experiment appended with hypothesis, primitive, design, measurement, verdict
- Never reordered or re-summarized
- Serialization is stable and cache-friendly

### S-8.5 — Conditional exclusions
Depends: S-8.4
Why: an exclusion recorded as fact permanently blocks the correct hypothesis.
AC:
- Every exclusion records its preconditions: fixture shape, platform, concurrency, scales tested
- When a later experiment changes a condition, affected exclusions are surfaced as stale
- The agent may re-test a stale exclusion
- A test proves a uniform-fixture exclusion is reopened when skewed fixtures are introduced

### S-8.6 — Evidence chain assembly
Depends: S-8.5
AC:
- Pydantic model requiring: symptom, exclusions, localization, mechanism, complexity, site, context, confidence
- **Every localization link requires an attached measurement** — schema rejects otherwise
- Context files each carry the reason they were implicated
- Golden-file test for serialization

### S-8.7 — Instrument switching — **the thesis behaviour**
Depends: S-8.1, S-3.1
AC:
- On a rejected hypothesis, the next hypothesis must select a different primitive where the evidence supports it
- Demonstrated end to end: a repo where query count is flat, the agent concludes "not the database," switches to ablation, and localizes the real cause
- The switch and its rationale appear in the experiment log

Notes: this is the demo that justifies the entire architecture. Record it as a video when it first works.

### S-8.8 — Reseed tool
Depends: S-7.7
AC:
- The Diagnostician can request new fixtures with a specified shape
- Reseeding invalidates affected exclusions per S-8.5
- Cost of reseeding counted against the experiment budget

### S-8.9 — Budget and progress
Depends: S-5.4
AC:
- 40-experiment cap
- Escalates after 8 experiments with no narrowing
- On exhaustion, emits a partial chain containing the exclusions — a proven negative is a result

---

# EPIC 9 — Finding audit

**Goal:** audit the diagnosis before any repair spend. **Build before the Surgeon.**

**Why this epic exists:** the patch audit checks equivalence, cheats, trades and scope. If the diagnosis is wrong, all of those pass — a correct fix to a non-problem is equivalent, is not a cheat, trades nothing, and breaks no callers.

### S-9.1 — Finding-audit invocation
Depends: S-8.6
AC:
- Adversary role invoked with the **raw experiment log**, not the assembled evidence chain
- Separate message list constructed fresh — no Diagnostician reasoning included
- Different model vendor where configured

Notes: giving it the raw log rather than the chain reduces framing inheritance. Isolation is partial, not clean — document it as such.

### S-9.2 — Exclusion validity attack
Depends: S-9.1
AC:
- Checks whether ruled-out hypotheses were ruled out under adequate conditions
- Flags exclusions whose preconditions were too narrow

### S-9.3 — Fixture adequacy attack
Depends: S-9.1
AC:
- Assesses whether fixture shape could have hidden the real cause
- Can request a re-run under different fixture shape

### S-9.4 — Scale adequacy attack
Depends: S-9.1
AC:
- Checks whether tested scales were large enough to separate linear from superlinear
- Flags fits with poor r² or too few points

### S-9.5 — Alternative explanation attack
Depends: S-9.1
AC:
- Proposes a different mechanism consistent with the same measurements
- If one exists and was not excluded, verdict is `unsound`

### S-9.6 — Reproducibility check
Depends: S-9.1, S-5.1
AC:
- Re-runs one key experiment, bypassing the replay cache
- Compares against the recorded result
- Material divergence produces `unsound`

### S-9.7 — Representativeness assessment
Depends: S-9.1
AC:
- Assesses whether the workload resembles something users exercise
- Verdict `unrepresentative` skips to the next finding without repair spend
- Limitation documented: the agent cannot know real traffic patterns

### S-9.8 — Verdict and routing
Depends: S-9.2–S-9.7
AC:
- Verdict schema: `sound` / `unsound` + objection / `unrepresentative` + reason
- `unsound` returns to investigate with the objection in context
- Cost of the audit is under 15 calls

---

# EPIC 10 — Surgeon agent

**Goal:** fix the confirmed finding, test first.

### S-10.1 — Falsification test generation
Depends: S-9.8
AC:
- First output is a test, not a patch
- Test asserts both cost improvement and correctness preservation
- Test enumerates the cheat classes it is designed to catch

### S-10.2 — Must-fail gate
Depends: S-10.1
AC:
- Test runs against unpatched code before any patch is written
- If it passes, the story stops with a report — no patch is written
- A test proves this gate fires on a vacuous falsification test

### S-10.3 — Test audit before patch
Depends: S-10.1, S-9.1
Why: the test is written by the agent that then writes the patch.
AC:
- Adversary reviews the falsification test **before** patch generation
- Question asked: could a cheat pass this test?
- If yes, the Adversary supplies a strengthened test and the Surgeon must satisfy it
- Costs under 5 calls

### S-10.4 — Patch generation
Depends: S-10.3, S-2.4
AC:
- Scope determined by the evidence chain's context list, not agent guessing
- Multi-file patches supported
- Runs in candidate mode only
- Patch applier rejects protected paths

### S-10.5 — Retry discipline
Depends: S-10.4
AC:
- Three attempts maximum
- **Structural check**: attempt 2 is rejected before running gates if its diff touches the same lines with a similar edit shape as attempt 1
- Failure reasons carried in context
- Temperature raised on retries to force different approaches
- Escalates with full attempt history after three

Notes: "must differ in approach" cannot be self-judged — the agent writes its own approach label and can rename the same idea.

### S-10.6 — Slack-reducing classifier — **SAFETY**
Depends: S-10.4
Why: our own output is the class of change that pushes systems from stable into vulnerable.
AC:
- Pattern-matches the diff for: added cache or memoization, retry logic, connection reuse, pool size reduction, timeout reduction, added buffering
- Matched patches labelled `slack-reducing`
- **Auto-approval permanently blocked** for labelled patches, at any trust level
- Emits a specific staging warning naming what headroom was removed
- Where fault injection is available (S-3.16), runs the retry-amplification check and attaches the result
- **Does not claim the patch was tested for metastability**

---

# EPIC 11 — Patch audit

**Goal:** defeat the patch, not review it.

### S-11.1 — Isolated context
Depends: S-10.4
AC:
- Receives original code, patched code, evidence chain, falsification test
- Never receives Surgeon reasoning, rationale, or prior attempts
- Isolation enforced by fresh message-list construction, not by prompt instruction
- Different model vendor where configured

### S-11.2 — Equivalence attacks
Depends: S-11.1
AC:
- Constructs adversarial inputs: empty collections, nulls, duplicates, ties, unicode, boundary sizes, unordered results
- Runs both revisions and diffs outputs
- On difference, returns a reproducing input

### S-11.3 — Cheat detection
Depends: S-11.1
AC:
- Checks for cached state across runs, deferred work, over-fetching, stubbed responses, shape-specific special-casing
- Verifies the improvement survives a fresh process

### S-11.4 — Trade attacks
Depends: S-3.8
AC:
- Checks the global resource envelope, not just declared guard pairs
- Reports what increased alongside what decreased

### S-11.5 — Scope attacks
Depends: S-11.1
AC:
- `find_callers` locates other call sites of every modified symbol
- Runs the full test suite
- Reports callers outside the tested workload

### S-11.6 — Test-quality attack
Depends: S-11.1
AC:
- Asks whether a cheat could pass the Surgeon's falsification test
- If yes, writes the test that would catch it
- Strengthened test becomes the permanent regression test

Notes: this is the deepest move in the design. The Adversary audits the verifier, not only the artifact.

### S-11.7 — Verdict
Depends: S-11.2–S-11.6
AC:
- Schema: `clean` / `broken` + reproducing input / `suspicious` + concern
- `broken` requires a reproducing input — schema-enforced
- Two rounds maximum, then escalate

### S-11.8 — Adversary ablation study
Depends: S-11.7
Why: if the Adversary adds little, it is theatre and should be cut.
AC:
- Runs a set of findings with and without the Adversary
- Counts bad patches reaching a human in each condition
- Reports the delta
- Repeats at two model tiers to test whether the mid tier misses attack classes

---

# EPIC 12 — LangGraph orchestration

**Goal:** durable execution across hours, crashes, and multi-day human gates.

### S-12.1 — Graph assembly
Depends: E7–E11
AC:
- Seven nodes wired: ground, screen, investigate, audit_finding, repair, audit_patch, ship
- Four routing functions implemented per `08-audit.md`
- Graph compiles and runs end to end on the target repo

### S-12.2 — Checkpointing
Depends: S-12.1, S-6.3
AC:
- SQLite checkpointer in development, Postgres supported for concurrent campaigns
- State persisted after every node
- Checkpoint size bounded per S-6.3

### S-12.3 — Crash resume
Depends: S-12.2
AC:
- A run killed mid-investigation resumes from the last checkpoint with full state
- Tested by killing the process at three different nodes
- Resumed run produces the same final result as an uninterrupted run

### S-12.4 — Human interrupt at ship
Depends: S-12.2
AC:
- `interrupt_before=["ship"]` at trust level 0
- The human sees the evidence chain, the patch, before/after measurements, and the Adversary verdict
- Resume after an arbitrary delay works

### S-12.5 — Early human checkpoint
Depends: S-12.4, S-9.8
Why: otherwise the human arrives after all cost is spent.
AC:
- Optional interrupt after the finding audit, before repair
- The human sees what was found and why, before any fix is attempted
- Enabled at trust level 0, skipped at higher levels

### S-12.6 — Time travel
Depends: S-12.2, S-6.2
AC:
- Rewind to an earlier checkpoint restores checkpointed state
- **Persistent store is unaffected** — failure memory from the later state survives
- A test rewinds after a failed repair and asserts the agent does not repeat the same approach

---

# EPIC 13 — Memory and trust

### S-13.1 — Playbook store
Depends: S-6.2, S-7.1
AC:
- Entries keyed by framework fingerprint
- Structured: situation, action, outcome
- Retrieved into Explorer context at grounding

### S-13.2 — Provisional writes and promotion — **SAFETY**
Depends: S-13.1
Why: a wrong entry propagates silently to all future runs and compounds.
AC:
- New entries are provisional and carry success/failure counters
- Promotion to trusted requires N successes **across different projects**
- Two failures demote and quarantine
- Entries are fingerprint-scoped, never global
- A test proves a wrong provisional entry does not affect a different project

### S-13.3 — Failure memory
Depends: S-6.2
AC:
- Rejected approaches recorded per finding
- Carried into retry context
- Survives rewind

### S-13.4 — Trust ledger
Depends: S-6.2
AC:
- Levels 0–2 per fix category
- **Keyed by project shape characteristics, not category alone**
- New projects start at level 0 regardless of cross-project history
- Cross-project history shown as advisory context
- Any revert or rejection demotes one level

Notes: a `select_related` fix approved 50 times on narrow-table projects may be wrong on a wide-table project. Trust learned elsewhere is context, not authority.

### S-13.5 — Learning curve measurement
Depends: S-13.1
AC:
- Explorer steps to first runnable workload recorded per project
- Plotted against number of projects with that fingerprint
- Should decline; if it does not, memory is not working
- **A controlled ablation of the playbook itself**: ground the same unseen repository with the playbook retrieved and with it withheld, and report the difference in stage completion and steps-to-ground

Notes: the curve is longitudinal and confounded — it declines if the playbook works, and also if later projects happen to be easier. The ablation is the causal measurement, and it is the project's own core primitive applied to the project. It also serves as a regression test: if a playbook edit stops helping, the delta shrinks and that is visible. S-0.4 supplies the method — interleave conditions and require a guard counter (here, stage completion) to move alongside the timing. **Do not copy S-0.4's fixed warm-up discard**: that spike hard-coded one, which S-1.2 forbids for good reason (Barrett et al. found at most 43.5% of VM/benchmark pairs reach steady state at all, so 'discard the first N' is an assumption that is wrong more often than not). Record whether each sample ran in a fresh or reused process and let the analysis decide.

---

# EPIC 14 — Adapters and MCP

### S-14.1 — Adapter interface
Depends: E1–E4
AC:
- Formal interface: `discover_workloads`, `seed`, `run_workload`, `run_tests`, `read_source`, `apply_patch`, `reset_state`, `capabilities`
- Declares hook points, framework-internal frames, protected paths, ORM dialect
- Defined as a Protocol with full typing

### S-14.2 — Django adapter
Depends: S-14.1
AC:
- Implements the full interface for Django + DRF + Postgres
- Query hook via `execute_wrapper`
- Works on the target repo and the holdout repo

### S-14.3 — Second adapter
Depends: S-14.2
AC:
- SQLAlchemy or Flask adapter implementing the same interface
- **Core code unchanged** — asserted by a test that runs both adapters through the same pipeline

### S-14.4 — Adapter conformance suite
Depends: S-14.1
AC:
- A test suite any adapter must pass
- Covers all interface methods plus reset reliability and hook overhead
- Documented so a third party can implement an adapter

### S-14.5 — MCP server extraction
Depends: S-14.3, S-14.4
Why: MCP earns its place only when an adapter exists in a different language.
AC:
- Adapter interface exposed as an MCP server
- Orchestrator connects to it over the protocol
- A reference adapter in a non-Python language demonstrates cross-language operation
- Conformance suite runs against the MCP-hosted adapter

Notes: this is genuinely last. Adding a protocol between two components you wrote yourself is overhead.

---

# EPIC 15 — Evaluation

### S-15.1 — Diagnostic agreement harness
Depends: S-12.1
Why: the headline reliability number, and nobody publishes it for this domain.
AC:
- Runs diagnosis N times independently on one repo with cache disabled
- Reports agreement on the primary finding as a percentage
- Reports the distribution of alternative findings
- Runs at N=10 minimum

### S-15.2 — Benchmark runner
Depends: S-12.1
AC:
- Runs a defined subset of SWE-Perf instances
- Compares against expert patches
- Reports per-category, not aggregate
- Subset size and selection criteria stated openly

### S-15.3 — Cost report
Depends: S-5.3
AC:
- Euros per confirmed finding, per run, per project
- Broken down by phase and model tier
- Cache hit rate and escalation rate included

### S-15.4 — Failure catalogue
Depends: S-15.1
Why: more credible than a success rate.
AC:
- Records repos where nothing was found
- Records caught cheats with the diff and the attack that caught them
- Records diagnoses that flipped between runs
- Records groundings that failed and why
- Published alongside results

---

# EPIC 16 — Reporting

### S-16.1 — Evidence chain rendering
Depends: S-8.6
AC:
- Human-readable report from an evidence chain
- Includes the growth table, exclusions with reasons, and the causal site
- Readable in under two minutes without re-deriving the reasoning

### S-16.2 — Pull request generation
Depends: S-16.1, S-11.7
AC:
- PR body contains: before/after on every varied axis, the evidence chain, guard metrics showing what did not regress, test results, Adversary verdict, and round-one reproducing cases if any
- The falsification test is attached as a permanent regression test
- `slack-reducing` patches carry the staging warning prominently

### S-16.3 — Null result report
Depends: S-4.5
AC:
- Structured report naming workloads screened, thresholds applied, and why nothing was flagged
- Distinguishes "healthy" from "insufficient data to tell"

---

# EPIC 17 — Hardening and release

### S-17.1 — End-to-end run on the holdout repo
Depends: E12
AC:
- Full pipeline runs on the repo reserved in S-0.6, never used in development
- Produces either a finding with a merged-quality PR, or an honest null result
- No manual intervention beyond approvals

### S-17.2 — Documentation
Depends: S-17.1
AC:
- Installation, configuration, and the four-line project config
- Adapter authoring guide
- An honest limitations page derived from `07-use-cases.md` §10

### S-17.3 — Real repository trial
Depends: S-17.1
AC:
- Run against at least three open-source projects
- At least one finding submitted as a real PR with the evidence chain
- Outcomes recorded in the failure catalogue regardless of result

---

## Dependency-ordered build sequence

Condensed to a single path for someone working alone:

```
S-0.1 → S-0.3 → S-0.4 → S-0.5 → S-0.2 → S-0.6 → S-0.7
S-1.1 → S-1.2 → S-1.3 → S-1.4 → S-1.5 → S-1.6 → S-1.7
S-2.1 → S-2.2 → S-2.3 → S-2.4 → S-2.5 → S-2.8 → S-2.6 → S-2.7
S-3.1 → S-3.2 → S-3.4 → S-3.6 → S-3.9 → S-3.8
S-4.1 → S-4.2 → S-4.3 → S-4.4 → S-4.5
S-5.1 → S-5.2 → S-5.3 → S-5.4
      ← M2 reached: a useful tool with zero model calls
S-6.1 → S-6.2 → S-6.3
S-5.5 → S-5.6 → S-5.7 → S-5.8
S-7.1 → ... → S-7.10
S-8.1 → ... → S-8.6 → S-8.7
      ← M3 reached: the thesis demo
S-9.1 → ... → S-9.8
S-10.1 → ... → S-10.6
S-11.1 → ... → S-11.8
S-12.1 → ... → S-12.6
      ← M4 reached: the contribution
E13 → E14 → E15 → E16 → E17
```

**Remaining primitives** (S-3.3, S-3.5, S-3.7, S-3.10 through S-3.18) slot in wherever the Diagnostician needs them. The registry in S-3.1 is what makes that possible — each is an afternoon, not a refactor.

---

## What this backlog decides that the design docs did not

| Decision | Where |
|---|---|
| Implementation language and repo layout | S-0.1 |
| LLM SDK and vendor strategy | S-0.2 |
| Persistence technology, both stores | S-0.2, S-6.1, S-6.2 |
| Adapter interface as code | S-14.1 |
| Development target and holdout | S-0.6 |
| How the tool tests itself | S-0.7 |
| Schemas as validated models | S-4.1, S-8.6, S-11.7 |
