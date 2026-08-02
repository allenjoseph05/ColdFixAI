# S-0.4 — Does ablation produce clean deltas?

**Status:** complete — the delta is clean, with two qualifications that change stories
**Runs:** both experiments executed twice; see *Result 5 — replication*
**Run by:** Claude Opus 5, in the spike's Linux container
**Date:** 2026-08-02
**Subject:** `django-helpdesk` pinned at `3a22901`, the commit S-0.3 grounded
**Timebox:** ~1 day

---

## Verdict

**Ablation produces clean deltas. The design's core is sound on this evidence.**

Stubbing `followup_set` on `GET /api/tickets/?page_size=100` moved the median
from **1454.73 ms to 434.64 ms** — a 0.299× ratio, `p = 6.8e-08`, Cliff's delta
**−1.000**, meaning the 20 baseline samples and the 20 ablated samples do not
overlap at all. Not one ablated request was slower than the slowest baseline
request.

Measurement noise was **CV 3.97 % baseline, 4.41 % ablated**, and the calibration
sweep puts the practical detection floor at roughly **20 ms, about 6 % of a
350 ms baseline, at 20 repetitions per condition**. The effect measured here is
about **fifty times that floor**. There is enormous headroom.

**But two results qualify that, and both change downstream stories:**

1. The two stub strategies produced **statistically indistinguishable timings**
   while differing **six-fold in payload size**. Timing alone would have called
   them interchangeable. The guard counters are the only reason we know they are
   not.
2. The `p < 0.01` test **passed twice on noise** in the calibration sweep, at
   injections far too small to have caused the shift reported. Separability at
   n=20 is not sufficient on its own to justify a finding.

---

## Experimental design

| | |
|---|---|
| Endpoint | `GET /api/tickets/?page_size=100` |
| Ablation target | `followup_set` — a `FollowUpSerializer(many=True)` nested field |
| Dataset | 503 tickets, 3004 followups, 3002 attachments (fixture-shaped, synthesized) |
| Conditions | `baseline`, `replay` (recorded real value), `empty` (`[]`) |
| Repetitions | 20 per condition, **interleaved**, order rotated each rep |
| Discarded | 5 warm-up requests |
| Test | Mann-Whitney U, two-sided, tie-corrected, continuity-corrected |
| Effect size | Cliff's delta, with Romano et al. thresholds |

Three design choices are worth stating because they are not incidental:

**In-process test client, not HTTP to a live server.** The AC requires
interleaving, and interleaving exists to cancel drift that a 20-then-20 block
design absorbs into the delta. Interleaving request-by-request means toggling
the stub between consecutive requests, so the toggle has to live in the process
that serves them. An external server would force a restart per condition — block
design, which is the thing interleaving is for.

**The patch is installed once and switched by a module-level flag**, not applied
and removed around each request. Patching per request charges `setattr` cost to
the treatment conditions only. With a flag, all three conditions run identical
machinery and differ only where intended.

**Timing includes response rendering.** DRF responses are lazy; stopping the
clock at `client.get()` would exclude JSON serialization of the payload — and
payload size is exactly where `replay` and `empty` diverge. Excluding it would
have hidden the finding this spike's second half exists to produce.

---

## Result 1 — the ablation delta

### Guard counters (separate untimed pass, `force_debug_cursor` per ADR 008)

| Condition | Queries | Bytes | Tickets | Followups in payload |
|---|---|---|---|---|
| `baseline` | **1193** | 429 071 | 100 | 586 |
| `replay` | **507** | 432 558 | 100 | 600 |
| `empty` | **507** | 71 758 | 100 | 0 |

Query breakdown by table:

| Table | baseline | replay | empty |
|---|---|---|---|
| `helpdesk_followupattachment` | **586** | — | — |
| `helpdesk_customfield` | 504 | 504 | 504 |
| `helpdesk_followup` | **100** | — | — |
| session + auth + ticket | 3 | 3 | 3 |

### Timings

| Condition | Median | Mean | SD | **CV** | Min | Max |
|---|---|---|---|---|---|---|
| `baseline` | 1454.73 ms | 1452.50 ms | 57.71 ms | **3.97 %** | 1335.60 | 1568.26 |
| `replay` | 434.64 ms | 433.75 ms | 19.12 ms | **4.41 %** | 401.93 | 472.31 |
| `empty` | 438.14 ms | 436.50 ms | 18.93 ms | **4.34 %** | 403.13 | 471.24 |

### Separability

| Comparison | Median shift | Ratio | p | Cliff's δ | Verdict |
|---|---|---|---|---|---|
| baseline → replay | **−1020.09 ms** | 0.299× | 6.80e-08 | **−1.000** (large) | **SEPARABLE** |
| baseline → empty | **−1016.59 ms** | 0.301× | 6.80e-08 | **−1.000** (large) | **SEPARABLE** |
| replay → empty | +3.50 ms | 1.008× | 6.36e-01 | +0.090 (negligible) | indistinguishable |

`p = 6.8e-08` is the floor of a two-sided Mann-Whitney at n=20 per group — it is
what total non-overlap produces, and no amount of extra effect drives it lower.
Cliff's δ = −1.000 is the informative figure: complete separation.

---

## Result 2 — the two stub strategies, and why timing alone lied

**This is the second half of the AC, and the answer is more interesting than
either "yes" or "no".**

`docs/10-BACKLOG.md` S-3.4's note predicts the strategies differ: *"An
empty-collection stub measures the component plus all downstream work that
consumed its output; a replayed real value measures the component alone."*

**On timing, they were indistinguishable.** 434.64 ms versus 438.14 ms,
`p = 0.64`, Cliff's δ = +0.090 — negligible.

**On payload, they differ by 6×.** `replay` returned **432 558 bytes**, `empty`
returned **71 758 bytes**.

So the note's premise is confirmed — the strategies genuinely measure different
things — while its practical consequence is not, *on this endpoint*. The reason
is mechanical: the ablated component is **database-bound** (686 queries
eliminated, ~1020 ms), and the downstream work it feeds is **CPU-bound JSON
rendering of ~360 KB**, which costs about 3.5 ms and vanishes into noise. When
the component is three orders of magnitude more expensive than the work it
feeds, the choice of stub cannot show up in the timing.

**The consequence for the design is the opposite of reassuring.** Had this spike
measured only wall time — the obvious thing to measure — it would have concluded
"stub strategy does not matter" and S-3.4's requirement to record the strategy
would have looked like unnecessary bookkeeping. **The guard counters are the
only reason the right conclusion was reachable.** This is the project's
guard-counter invariant earning its place on the first occasion it was tested,
and it is worth noting the failure it prevented was not a wrong number but a
wrong *deletion of a requirement*.

### A limitation of fixed-value replay, found by the guard counters

`replay` returned **600 followups where the baseline returned 586**, and
**432 558 bytes where the baseline returned 429 071** — the ablated condition
emitted *more* data than the unablated one.

The cause: a single recorded 6-followup value is replayed for every ticket,
including the three demo-fixture tickets that really have one or two. Fixed-value
replay does not preserve per-instance cardinality, so it slightly inflates any
downstream cost proportional to volume.

Here that inflation is 0.8 % and harmless. It would not be harmless if the
replayed component fed something expensive, because the ablated run would then
be *charged more downstream work than the baseline ever did* — and the ablation
delta would understate the component's true cost. **S-3.4 should record the
cardinality gap alongside the strategy name**, which the guard counters already
compute.

### A methodological trap, hit and recorded

The first run of this experiment recorded a replay value of **one** followup,
because the first ticket with any followups is a demo-fixture ticket that sorts
ahead of the 500 synthesized ones. That made the replay payload nearly as small
as the empty stub's, and the two strategies looked interchangeable — the correct
final conclusion, reached by accident, for a completely wrong reason.

A replay stub that is not *size-representative* is not measuring the component
alone. It is measuring the component plus an unstated fraction of the downstream
work — which is the empty stub's semantics wearing a disguise. The harness now
records the value whose length is closest to the page median and prints the
distribution it chose from. **S-3.4 needs the same guard**, or its two strategies
will silently collapse into one.

---

## Result 3 — ablation revealed a second N+1 underneath the first

Ablating `followup_set` removed 686 queries and left **507**, of which **504 are
`helpdesk_customfield`** — approximately one per ticket. That is a second,
independent N+1 which was completely invisible while the first one dominated.

This is the localization loop from `01-primitives.md` §7 working on its first
real attempt: remove the dominant component, and the next bottleneck becomes
both visible and measurable. It also means **the residual after ablation is a
finding in its own right**, not merely a baseline — worth surfacing to the
Diagnostician rather than discarding.

The harness groups residual queries by table for exactly this reason. An
ablation that removes one N+1 and leaves another standing is a good result, but
only if the second one can be named.

---

## Result 4 — calibration: how small a delta can this method detect?

A 3.3× effect separating cleanly proves little; almost any method would find it.
The story's stated worry is the opposite case, so the useful number is the
**minimum detectable difference**.

Method: take the ablated endpoint as a base, inject a *known* delay at the same
patch point, and sweep downward. The injection point fires **exactly 100 times
per request** (counted, not inferred — see the correction below), so a per-call
delay is multiplied 100× at the response level.

| Injected/call | Expected shift | Base median | Test median | **Measured shift** | Error | CV base | p | Cliff's δ | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1.000 ms | 100.0 ms | 354.2 ms | 456.7 ms | **+102.6 ms** | +2.6 | 6.01 % | 6.80e-08 | +1.000 | SEPARABLE |
| 0.500 ms | 50.0 ms | 342.5 ms | 410.1 ms | **+67.6 ms** | +17.6 | 3.78 % | 6.80e-08 | +1.000 | SEPARABLE |
| 0.300 ms | 30.0 ms | 351.0 ms | 385.0 ms | **+33.9 ms** | +3.9 | 7.94 % | 1.12e-03 | +0.605 | SEPARABLE |
| 0.200 ms | 20.0 ms | 352.4 ms | 382.0 ms | **+29.6 ms** | +9.6 | 4.14 % | 1.80e-06 | +0.885 | SEPARABLE |
| 0.100 ms | 10.0 ms | 347.8 ms | 366.0 ms | +18.2 ms | +8.2 | 9.77 % | 1.67e-02 | +0.445 | not separable |
| 0.050 ms | 5.0 ms | 341.3 ms | 361.7 ms | +20.4 ms | **+15.4** | 5.63 % | 1.63e-03 | +0.585 | **SEPARABLE — false** |
| 0.020 ms | 2.0 ms | 340.1 ms | 358.8 ms | +18.7 ms | **+16.7** | 4.40 % | 6.04e-03 | +0.510 | **SEPARABLE — false** |
| 0.010 ms | 1.0 ms | 343.1 ms | 365.9 ms | +22.8 ms | **+21.8** | 6.03 % | 2.56e-02 | +0.415 | not separable |
| 0.000 ms | 0.0 ms | 348.3 ms | 340.8 ms | −7.4 ms | −7.4 | 6.62 % | 2.39e-01 | −0.220 | not separable |

**Read the bottom four rows as measuring the injector, not the injection.** The
measured shift sits at 18–23 ms whether 10, 5, 2 or 1 ms was requested. That is
a constant, and constants are not responses.

### The injector has its own floor

Measured directly, in the same container:

| Requested per call | 100× requested | 100× actual | **Overhead** |
|---|---|---|---|
| 0 µs | 0.00 ms | 7.54 ms | **7.54 ms** |
| 10 µs | 1.00 ms | 8.91 ms | **7.91 ms** |
| 50 µs | 5.00 ms | 13.42 ms | **8.42 ms** |
| 200 µs | 20.00 ms | 28.92 ms | **8.92 ms** |
| 1000 µs | 100.00 ms | 108.89 ms | **8.89 ms** |

`time.sleep` costs ~80–100 µs per call in syscall overhead **regardless of the
duration requested** — `time.sleep(0)` is not free. At 100 calls per request
that is a fixed ~8–10 ms floor on every injected condition. The sweep therefore
bounds the detection floor from above but cannot resolve below it.

### False-positive rate — bounding the floor from below

The sweep's two false "SEPARABLE" verdicts needed explaining, so the rate was
measured directly: the identical condition compared against itself, 12 times, no
`time.sleep` involved anywhere.

| | |
|---|---|
| Trials | 12 |
| **False positives** | **0 / 12 (0 %)** |
| Largest spurious shift observed | **12.76 ms** |
| Null shifts (ms) | −10.35, +1.95, +3.92, −2.20, −12.76, −2.02, +2.15, +2.02, −0.36, +2.46, +6.30, +7.84 |

**The test itself is well calibrated.** Zero false positives in twelve trials
under tight alternation. The sweep's two false verdicts came from the injector's
~9 ms overhead being real added work — the test correctly detected a real shift
that was not the shift being asked about.

### Detection floor

| | |
|---|---|
| Largest shift observed under the null | **12.76 ms** |
| Injector overhead floor | ~9 ms |
| Smallest cleanly separable real effect | **~20 ms (≈6 % of a 350 ms baseline)** |
| Measured ablation effect | **1020 ms (70 % of baseline)** — ~50× the floor |

---

## Result 5 — replication: what reproduces and what does not

Both experiments were run twice, the second time after the harness was
refactored, on the same container and dataset. The comparison is more useful
than either run alone.

| | Run 1 | Run 2 | Reproduced? |
|---|---|---|---|
| **Guard counters** (queries, bytes, rows) | 1193 / 429 071 / 586 | 1193 / 429 071 / 586 | **exactly, to the byte** |
| Baseline median | 1454.73 ms | 1275.15 ms | **no — 12.3 % apart** |
| Ablated median (`replay`) | 434.64 ms | 370.69 ms | no — 14.7 % apart |
| **Ratio** baseline → replay | 0.299× | 0.291× | **yes — 2.7 % apart** |
| Cliff's δ for the ablation | −1.000 | −1.000 | yes |
| `replay` vs `empty` | p=0.636, negligible | p=0.946, negligible | yes |
| False positives under the null | 0 / 12 | 0 / 12 | yes — **0 / 24 in total** |
| Largest spurious shift | 12.76 ms | 13.96 ms | yes, closely |
| Smallest separable effect | 18.68 ms (5.27 %) | 20.13 ms (5.58 %) | yes, closely |

**Absolute timings do not reproduce across runs; ratios, guard counters, and the
detection floor do.** The baseline moved 12 % between two runs minutes apart on
an idle machine, while the ratio derived from those same numbers moved 2.7 %.

This has a direct consequence for how findings should be *stated*. "This
component costs 1020 ms" is not reproducible and will not survive a rerun on
another machine. "This component accounts for 70 % of the endpoint's time, and
removing it leaves a 504-query residual" survives both. **Findings should be
expressed as ratios and counts, with absolute times as supporting detail rather
than as the claim.**

It also strengthens the guard-counter argument considerably. The counters were
identical to the byte across two runs, because they are deterministic functions
of the dataset and the code. A metric that reproduces exactly is a far better
foundation for an automated verdict than one that wanders 12 % on an idle
machine — which is an argument for guard counters being *primary* evidence and
timing being corroboration, rather than the other way round.

---

## Consequences for the build

| Change | Story | Why |
|---|---|---|
| **Record the cardinality gap** (rows replayed vs rows baseline emitted), not just the strategy name | S-3.4 | Fixed-value replay emitted 600 followups against a 586 baseline. Undetectable without guard counters, and it biases the delta downward when downstream work is expensive |
| **The recorded replay value must be size-representative** — median, not first-found | S-3.4 | A first-found value collapsed replay into the empty stub and produced the right answer for the wrong reason |
| **Separability must never justify a finding on its own.** Require a guard counter to move in a consistent direction | S-4.x, S-8.x | Two sweep rows passed `p < 0.01` on a shift the injection could not have caused. Query count and bytes were unchanged; a guard-counter check rejects both |
| **Surface the post-ablation residual as a candidate finding** | E7 / S-3.5 | Ablating one N+1 exposed a 504-query second one. The residual localizes the next experiment |
| **20 reps is enough here, but record CV per condition** and treat deltas under ~10 % of baseline as requiring more | S-3.4 | CV ran 3.8–9.8 % across conditions. The floor is a function of it, not a constant |
| **Never derive a constant from a timing probe when it can be counted** | E1 | The first calibration inferred 121 calls/request from 5 noisy samples; the true figure is exactly 100, inflating every expectation by a fifth |
| **Prefer non-parametric tests and report effect size** | E1 | Latency is right-skewed; a single spike moves a t-statistic much further than it moves reality |
| **State findings as ratios and counts; absolute times are supporting detail** | S-4.1, E11 | Baseline median moved 12 % between two runs minutes apart; the ratio from the same numbers moved 2.7 % |
| **Treat guard counters as primary evidence, timing as corroboration** | S-4.1 | Counters reproduced byte-identically across runs; timings did not |

### Backlog correction

`S-0.4`'s `Notes:` line reads *"this is what motivates **S-3.3**'s
record-and-replay requirement."* Record-and-replay is **S-3.4** — S-3.3 is
*Scaling: shape*. S-3.4's own `Depends:` line correctly lists S-0.4. Corrected
in this branch.

---

## Bounds on this verdict

- **One endpoint, one repository, one shape of defect.** The ablated component
  was database-bound with cheap downstream work. A CPU-bound component feeding
  expensive downstream work would very likely separate the two stub strategies
  on timing, and this spike says nothing about that case. It is the case S-3.4
  most needs.
- **The container was otherwise idle.** Real runs will contend with the subject's
  own background work. The 12.76 ms largest spurious shift is a floor for a quiet
  machine, not a busy one.
- **Uniform fixtures by construction.** Every ticket has exactly 6 followups.
  That was deliberate — skew would make per-request work depend on which tickets
  a page contained, adding spread unrelated to the instrument. It also means
  this spike cannot speak to S-3.3's concern, which is precisely that uniform
  data hides skew-dependent defects.
- **`p = 6.8e-08` is a floor, not a measurement.** It is what total non-overlap
  produces at n=20. Cliff's delta is the figure that carries information.

---

## Follow-on

- **S-0.5 (state reset)** can reuse this environment directly — same subject,
  same scaled dataset, database already at a known row count. The
  `_guard_counters` pass is a ready-made "are the row counts identical" probe.
- **S-3.4** inherits three concrete requirements from Result 2, listed above.
- **`tests/fixtures/` (S-0.7)** should plant *a component whose downstream work
  is expensive*, since that is the case this spike could not exercise and the
  one where stub strategy actually changes the number.
