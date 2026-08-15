# Planted-defect fixtures

Deliberately defective code with **known measurement signatures**, for testing
the instruments that will look for defects like these.

Built by S-0.7. `tests/test_planted_defects.py` asserts every signature below,
because a measuring standard nobody calibrated is worse than none — every
downstream test inherits its error silently.

---

## Why this is pure Python and not a small Django app

These fixtures exist to test *instruments* against ground truth. A query counter
is correct when it reports 21 and exactly 21 queries were issued, and
establishing that needs a subject whose true count is known **by construction**
rather than measured.

Realism is a different job, and it already has an owner: the pinned development
target in ADR 011 is a real 1.7k-star Django application with a real unplanted
N+1. That is where real SQL, a real planner and real connection behaviour get
exercised.

The consequences of the split, stated so nobody re-litigates it:

- Tests run in milliseconds with no service to start. The fast subset stays fast,
  and these fixtures cannot fail for environmental reasons.
- Every count is exact and deterministic. S-0.4 measured wall-clock timings
  drifting 12% between runs minutes apart while guard counters reproduced to the
  byte; these fixtures have only the second kind of number.
- **They cannot catch anything requiring real SQL.** That is a real limitation,
  and it is why integration tests against the target are not optional.

---

## Every defect has a control

The controls are the load-bearing part of this fixture, not padding.

A detector that reports "N+1" unconditionally passes every defect test. Only a
clean counterpart — same purpose, same output, different cost — can fail it.

This is the S-0.6 holdout argument applied one level down. A holdout containing
a defect measures whether a tool generalizes; a holdout where the right answer is
*nothing found* measures whether it can resist manufacturing one. Two project
invariants depend on getting that second thing right:

> **Null results are valid output.** Never manufacture a finding.

---

## The catalogue

### Query defects — `planted/queries.py`

| Function | Kind | Signature |
|---|---|---|
| `list_books_n_plus_one` | **defect** | `queries == 1 + A`, grows with the dataset |
| `list_books_batched` | control | `queries == 2`, constant at any size |
| `list_titles_over_fetching` | **defect** | `queries == 1`, `cells_returned` 5× the control |
| `list_titles_narrow` | control | `queries == 1`, minimal payload |
| `summarize_with_fixed_floor` | **decoy** | `queries == 37`, **constant** — expensive but correct |
| `render_with_expensive_downstream` | **defect** | `queries == 2`, cost is downstream of the fetch |

Three of these deserve explanation.

**`list_titles_over_fetching` is invisible to query counting.** It and its
control both issue exactly one query. Only the guard counter separates them.
A tool measuring one dimension reports nothing here and is wrong — the same
asymmetry S-0.4 hit from the other direction, where two ablation strategies were
indistinguishable on timing while differing six-fold in payload.

**`summarize_with_fixed_floor` must never be flagged.** It issues 37 queries
regardless of dataset size, modelled on the ~35-query floor S-0.3 measured on
netbox — the shape a mature system actually has. At small sizes it costs *more*
than the real N+1 beside it, so absolute query count ranks the two backwards.
Only growth rate gets it right, which is why the signature in ADR 011 is a
formula and not a number. A "fix" here would be the metastability trap
`00-BRIEF.md` §4 warns about.

**`render_with_expensive_downstream` fills the gap S-0.4 could not test.** There,
the ablated component was database-bound and the work it fed was cheap, so replay
and empty stubs gave the same answer. Here the ratio is inverted, so the two stub
strategies should diverge — the case S-3.4 most needs and no real subject has
supplied.

### Complexity — `planted/loops.py`

Operation counts, not timings. A curve fitter validated against wall-clock time
on these would be measuring the machine as much as the algorithm.

| Function | Complexity | Operations |
|---|---|---|
| `constant_lookup` | O(1) | `1` |
| `linear_scan` | O(n) | `n` |
| `linearithmic_sort` | O(n log n) | control — must not be called quadratic |
| `quadratic_pairs` | **O(n²)** | `n²` exactly |
| `quadratic_membership` | **O(n²)** | `n(n-1)/2`, hidden behind `x in list` |

`quadratic_membership` is the interesting one. It has no visible nested loop —
it is how a quadratic actually appears in real code — and its doubling ratio
approaches 4 **from above** (4.22, 4.11, 4.05, …) rather than landing on it. A
fitter keying on an exact ratio rather than a fitted exponent gets it wrong.

`linearithmic_sort` exists so "recovers the exponent" is a real claim rather than
a two-way guess between linear and quadratic.

### Imports — `planted/slow_import.py`, `planted/fast_import.py`

| Module | Kind | Signature |
|---|---|---|
| `slow_import` | **defect** | cost paid at import, ≥5× the control |
| `fast_import` | control | near-free import, cost moves to first call |

Both expose the same `lookup()` and return identical answers, so only *when* the
cost is paid distinguishes them.

The work is CPU-bound rather than a `time.sleep`, for two reasons. S-0.4 measured
`time.sleep` carrying 80–100 µs of syscall overhead per call regardless of
duration, so a sleep-based fixture partly measures the sleep. And a real slow
import is module-level computation or a heavy dependency graph, not a pause.

Import cost is asserted as a **ratio against the control**, never as an absolute
duration — an absolute threshold encodes the speed of whichever machine wrote it.
Both import tests are marked `slow` and excluded from `pytest -m "not slow"`.

### Real-time screening — `realtime/`

Two whole repositories rather than two functions, because S-2.8 screens a tree
and not a call.

| Directory | Kind | Expected |
|---|---|---|
| `realtime/flight_controller` | **defect** | refused — RTOS imports, deadline annotations, certification markers, framework signatures |
| `realtime/task_tracker` | control | cleared — every innocent word a naive detector fires on |

The control is the valuable half here, more than anywhere else in this
catalogue. It is an ordinary web application with **deadlines** (including
"hard deadlines" in the contractual sense), a **priority** field whose highest
value is **critical**, a class named **Scheduler**, **real-time** updates
meaning websockets, and **mission-critical** work meaning somebody will be
annoyed. None of it is a timing guarantee.

The pinned development target in ADR 011 is a helpdesk application full of the
same vocabulary. A screening that refuses the control refuses its own target on
day one, so `test_the_control_repository_is_cleared` is the test that keeps the
tool usable, and a third test asserts the control still *contains* the tempting
words — because the way that claim quietly stops meaning anything is somebody
tidying the vocabulary out of the fixture rather than the detector changing.

---

## Growing this

The backlog is explicit:

> the planted-defect fixture repo is the single most useful test asset in the
> project. Build it early and **grow it whenever a real repo surprises you**.

Three surprises from the E0 spikes are **not** yet represented here, all of them
grounding-stage problems rather than measurement problems, and all needing a real
framework rather than this store:

- a `DATABASES` hardcoded with no environment override (S-0.3, obstacle B-1)
- a setting that warns at startup and only fails at use (S-0.3, obstacle C-5)
- a workload writing state **outside** the database, which no reset strategy in
  S-0.5 could undo

They belong with E2 and E7's tests, where a real framework is already in play.
Recorded here so they are not lost.

When adding a defect: give it a signature, give it a control, and assert both.
A defect without a control teaches a detector to say yes.
