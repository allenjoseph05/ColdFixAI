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

### S-2.5 — Production guard — **SAFETY**
Depends: S-0.1
AC:
- The system refuses to start unless the database URL matches a configured test pattern
- The check runs before any other initialization
- The error message states exactly what was expected and what was found
- No override flag exists

### S-2.6 — State reset strategies
Depends: S-0.5, S-2.1
AC:
- Three strategies implemented: transaction rollback, database snapshot restore, container restart
- Strategy is selectable per project and recorded in the workload artifact
- Each strategy is verified by the harness in S-2.7 before use

### S-2.7 — Reset verification harness
Depends: S-2.6
AC:
- Runs seed → workload → reset ten times
- Asserts row counts identical across all cycles
- Checks sequence counters and cache state, not just row counts
- Fails the workload with a clear diagnostic if reset is unreliable, falling back to the next strategy

### S-2.8 — Real-time system detection and refusal — **SAFETY**
Depends: S-0.1
Why: measurement-based analysis is provably insufficient for WCET, and a caching optimization would improve every metric we measure while degrading worst-case timing.
AC:
- Detects RTOS imports, deadline annotations, safety-certification markers, and known real-time framework signatures
- On detection, refuses to proceed and explains why in one paragraph
- Detection runs before grounding, not after
- A test fixture with real-time markers is refused

Notes: this is the only category where running the system could make things worse while reporting success. Ship it before the tool is ever pointed at an unfamiliar repository.

### S-2.9 — Scope refusals
Depends: S-2.8
AC:
- Concurrency and locking findings are marked `diagnose-only` and can never enter the repair path
- Causes localized inside third-party dependencies are reported, never patched
- Unsupported project types (frontend, mobile, embedded, mainframe) are detected where possible and reported honestly

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

### S-3.2 — Scaling: volume
Depends: S-1.5, S-2.6
AC:
- Runs a workload at three or more scale points with reset between each
- Fits every recorded metric against the scale variable
- Subtracts the framework baseline measured at N=0
- Forces lazy results to materialize before measurement stops
- Clears caches between scale points

Notes: the three failure modes above (baseline offset, lazy evaluation, warm cache) each silently produce a wrong answer. Test each.

### S-3.3 — Scaling: shape
Depends: S-3.2
Why: uniform fixtures hide skew-dependent defects at every volume.
AC:
- Generates fixtures with configurable distribution: uniform, power-law, long-tail
- Can hold volume constant while varying distribution
- Distribution used is recorded in every measurement
- A test proves a skew-dependent defect is invisible under uniform data and visible under power-law

Notes: an N+1 that costs milliseconds at three related rows and minutes at three thousand is invisible if every generated parent has exactly three children.

### S-3.4 — Ablation with record-and-replay stubs
Depends: S-2.3, S-0.4
AC:
- Records a real return value for the target during a baseline run
- Replays that value during ablation
- Falls back to a minimal valid value where replay is impossible (streams, stateful objects)
- **Records which strategy was used** in the experiment result
- Runs only in diagnostic mode — enforced, not conventional

Notes: stub choice changes what is measured. An empty-collection stub measures the component *plus* all downstream work that consumed its output; a replayed real value measures the component alone. The interpretation differs, so the strategy must be recorded.

### S-3.5 — Delta debugging search over ablation targets
Depends: S-3.4
Why: sequential guessing is O(n); binary search over subsets is O(log n).
AC:
- Implements `ddmin` with a threshold oracle (cost exceeds X) rather than a boolean crash oracle
- Implements the `dd` variant that isolates the difference between a fast and a slow case
- Localizing among 40 candidates completes in materially fewer than 40 ablations on a synthetic test
- Handles the case where removing a subset breaks the workload entirely

Notes: prefer `dd` over `ddmin` — we usually have both a fast case and a slow case, and isolating the difference is exactly what `dd` does.

### S-3.6 — Observation: on-CPU counters
Depends: S-1.3
AC:
- Counters for: database queries, rows returned, bytes returned, HTTP requests, file opens, allocations
- Each counter attaches via a framework-specific hook declared by the adapter
- Stack capture per event, optional
- Counter overhead verified under 5%

### S-3.7 — Observation: off-CPU
Depends: S-3.6
Why: without it the entire saturation column of the USE Method is unmeasurable.
AC:
- Measures time blocked on: disk I/O, network, lock acquisition, scheduler queueing
- Distinguishes "computed a lot" from "waited a lot" in the experiment result
- Works inside the container sandbox
- A test with a deliberate sleep shows blocked time, not CPU time

Notes: an ablation without this tells you a component is expensive but never whether it computed or waited — and those have nothing in common as fixes.

### S-3.8 — Guard counters and global resource envelope
Depends: S-3.6
Why: guard pairs are a denylist and fail by omission.
AC:
- Every primary counter has a declared guard counter
- **Additionally**, every candidate is measured against a global envelope: peak RSS, total CPU, wall time, bytes written, file descriptors, process count
- Any envelope metric outside tolerance triggers a flag regardless of whether that trade was predicted
- A test proves a patch trading queries for a memory explosion is flagged

### S-3.9 — Stack localization
Depends: S-3.6
AC:
- Normalizes stacks by stripping framework-internal frames from an adapter-supplied deny list
- Groups events by normalized signature
- Walks to the divergence point — the deepest frame common to all occurrences
- Emits the causal site plus the dependency closure (models, relationship declarations, consumers, callers)
- Handles async boundaries where context is lost, or reports that it cannot

Notes: this is how findings span multiple files without the agent reading the repository. The runtime names the files.

### S-3.10 — Substitution
Depends: S-1.6
AC:
- Swaps an implementation or configuration value and re-measures
- Configuration substitution supports sweeping a range of values
- Every substitution is reversible and reverted after measurement
- Query-plan comparison supported for index hypotheses

### S-3.11 — Temporal
Depends: S-2.2, S-1.6
AC:
- Checks out an earlier revision in a worktree and runs the same workload
- Bisects across a commit range to find a regression point
- Handles revisions that fail to build by reporting and skipping
- Verifies the workload exists at both endpoints before starting

### S-3.12 — Load with USL fitting
Depends: S-1.5
AC:
- Drives concurrent load at increasing levels at fixed data size
- Fits throughput against concurrency, returning contention (α), coherency (β), and Nmax
- Cross-checks measurements against Little's Law for self-consistency
- Findings are marked `diagnose-only`

Notes: the fitted coefficients are diagnostic, not just descriptive — high α points at a shared resource, high β at coordination cost. Surface them to the agent, not just the curve.

### S-3.13 — Isolation
Depends: S-3.12
AC:
- Runs a component standalone and in full context, reporting the gap
- Findings marked `diagnose-only`

### S-3.14 — Proportional perturbation
Depends: S-3.4
AC:
- Injects a known fractional slowdown into a target and measures the effect
- **Applicability predicate returns false for single-threaded synchronous code**
- Returns a sensitivity curve, not a single point

Notes: Coz's virtual speedup works by pausing concurrently running threads. In single-threaded code there is nothing to pause and the primitive degenerates into ablation. Gate it.

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
