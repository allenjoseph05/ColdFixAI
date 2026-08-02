# S-0.5 — Is state reset reliable?

**Status:** complete — reset is reliable, but **not** by the method the AC assumes, and **not** verifiable by the check the AC specifies
**Run by:** Claude Opus 5, in the spike's Linux container
**Date:** 2026-08-02
**Subject:** `django-helpdesk` at `3a22901`, same dataset as S-0.4 (503 tickets / 3004 followups / 3002 attachments; 37 tables, 6881 rows, 36 sequences)

---

## Verdict

**Use transaction rollback followed by an explicit sequence restore.** It is the
only strategy that is both correct and cheap.

| Strategy | Clean cycles | Row counts | Content | **Median reset** | Min | Max |
|---|---|---|---|---|---|---|
| `rollback` | **0 / 10** | identical | identical | **0.4 ms** | 0.3 | 0.7 |
| **`rollback+setval`** | **10 / 10** | identical | identical | **19.2 ms** | 17.1 | 26.9 |
| `template` | 10 / 10 | identical | identical | 163.3 ms | 140.9 | 408.2 |
| `dump_restore` | 10 / 10 | identical | identical | 2022.0 ms | 1891.4 | 2154.4 |

`rollback+setval` is **8.5× faster than a template copy** and **105× faster than
a dump/restore**, and it passed all ten cycles on every check.

**Two results matter more than the ranking:**

1. **Plain `rollback` failed all ten cycles — and the AC's own check would have
   passed it.** Row counts were identical every cycle. So were content hashes and
   maximum ids. The sequences were not.
2. **The story's note is right about two of the three survivors it names, and
   wrong about the third.** Sequence counters and cached querysets do survive a
   rollback. Postgres session state does not.

---

## Result 1 — the AC's check is too weak to detect the failure

The AC says: *"Assert row counts across all tables are identical every cycle."*

Plain `rollback` satisfies that perfectly. Across ten cycles, all 37 tables held
exactly the baseline row count, every content hash matched, and every `max(id)`
matched. By the specified check, rollback is a flawless reset.

It is not. After ten cycles:

| Sequence | Before | After | Drift |
|---|---|---|---|
| `helpdesk_ticket_id_seq` | 509 | **759** | **+250** |
| `helpdesk_followup_id_seq` | 3004 | **3504** | **+500** |

Exactly 25 tickets and 50 followups per cycle — the workload's insert count,
accumulated ten times over and never given back.

**Why this happens.** Postgres sequences are non-transactional by design.
`nextval()` takes effect immediately and is never rolled back, because two
concurrent transactions must never receive the same id — if `ROLLBACK` returned
numbers to the pool, that guarantee would break. This is correct database
behaviour, and it is precisely why it defeats a naive reset.

**Why it matters here.** The next experiment inserts rows with different primary
keys than the previous one. Anything ordered by id, keyed on id, paginated by
id, or comparing ids across runs behaves differently — and the difference is
invisible to row counting. For a system whose entire method is *"measure, change
one thing, measure again"*, a starting state that silently differs between
measurements is the failure mode most likely to produce a confident wrong answer.

**Consequence for the backlog:** S-0.5's AC as written would have shipped a
broken reset. The assertion needs to be *"row counts, content, maximum ids and
sequence values are all identical"*. Row counting alone is necessary and nowhere
near sufficient.

---

## Result 2 — the three named survivors, checked directly

The story's note says *"sequence counters, cached querysets, and connection-level
state commonly survive a rollback. Check for those specifically."* Each was
probed on its own rather than inferred from the cycle results.

### Sequence counters — **survive, confirmed**

```
helpdesk_ticket_id_seq: 509 -> 510 after a single rolled-back INSERT
leaked = True
```

One insert, rolled back, and the sequence is permanently one higher.

### Cached querysets — **survive, confirmed**

A Django `QuerySet` caches its rows in the Python object the first time it is
evaluated. A database rollback cannot reach into that object.

```
rows cached in the Python object : 1
rows actually in the database    : 0
leaked = True
```

The queryset was evaluated inside the transaction, the transaction was rolled
back, and the Python object still reports a row that no longer exists anywhere.

**This one is not fixable by any database-side reset.** Template copy and
dump/restore would leave it equally stale — it is not in the database. The only
remedy is to discard Python-side state between cycles, which means the reset
contract has to cover the *process*, not just the database.

### Connection-level state — **does not survive; the note is wrong here**

```
set inside the transaction : coldfix-probe-inside-txn
value after rollback       : coldfix-probe
leaked = False
```

A session `SET` executed inside a transaction **is** reverted when that
transaction rolls back. This surprised the note and is worth stating precisely,
because the general belief that session state is non-transactional is what the
note encodes.

In Postgres, `SET` is transactional: its effect disappears if the surrounding
transaction aborts. (`SET LOCAL` differs in *scope* — it ends at commit — not in
whether rollback reverts it.) So for this database, connection-level GUCs are one
thing the reset does **not** have to handle.

**This does not generalize.** It is a property of Postgres, and the first
adapter targets Postgres. Other connection state — server-side prepared
statements, advisory locks, temporary tables, and the client library's own
buffers — is a separate question this probe does not answer. The claim proved
here is narrow: *session configuration parameters set inside a rolled-back
transaction do not survive it.*

---

## Result 3 — cost, measured comparably

All four numbers time **only the reset**, not the workload that preceded it.

The first version of this harness did not do that: it wrapped
`transaction.atomic()` in the timer for the rollback strategy while the other
strategies ran their workload outside it. That charged the workload to rollback
and reported 166.9 ms where the true figure is **0.4 ms** — a 400-fold error, in
the direction that would have made rollback look comparable to a template copy
and hidden the fact that repairing it is nearly free. Recorded because the
mistake is not obvious and the corrected numbers are the whole basis of the
recommendation.

| | `rollback` | `rollback+setval` | `template` | `dump_restore` |
|---|---|---|---|---|
| Correct? | **no** | **yes** | yes | yes |
| Median | 0.4 ms | **19.2 ms** | 163.3 ms | 2022.0 ms |
| Relative to `rollback+setval` | 48× cheaper, wrong | — | 8.5× dearer | 105× dearer |
| Scope of reset | current transaction | current transaction + all sequences | whole database | whole database |
| Needs exclusive access? | no | no | **yes** — terminates other connections | no |

`template`'s spread is worth noting: 140.9 ms to 408.2 ms, far wider than the
others, because it must terminate every existing connection and drop the
database. It also cannot run while anything else is connected, which makes it
unusable for any concurrent experiment design.

`dump_restore` is the only strategy that survives the database server being
replaced, so it remains the right tool for establishing a baseline snapshot
across machines — but at two seconds per cycle it cannot be the per-experiment
reset. `pg_dump` itself took 262 ms; the restore is what costs.

---

## Recommendation for the build

**Per-experiment reset: `rollback+setval`.** Correct on every check, 19.2 ms, no
exclusive access required, and it composes with concurrency in a way `template`
cannot.

The implementation is exactly what the harness does:

1. Capture every sequence's `last_value` and `is_called` once, at baseline.
   `pg_sequences.last_value` is NULL for a never-used sequence, which cannot be
   passed to `setval` — those restore to `start_value` with `is_called=false`,
   or the first `nextval` after a reset skips a number.
2. Run the workload inside an explicit transaction.
3. `ROLLBACK`.
4. `setval` every sequence back to its captured value.

**But the reset contract must cover the process, not only the database.** The
cached-queryset probe shows Python-side state surviving every database strategy
tested. Any reset primitive that promises "clean starting state" while leaving a
live `QuerySet`, a populated Django cache, or a memoized value in place is making
a claim it cannot keep.

| Change | Story | Why |
|---|---|---|
| **Reset verification must assert row counts, content hashes, max ids *and* sequence values** | S-0.5 AC, S-2.x | Row counts alone passed a reset that failed 10/10 |
| **`reset()` restores sequences explicitly after rollback** | E2 | The defect is silent and cheap to fix |
| **The reset contract covers process state, not just the database** | E2 | Cached querysets survive every database-side strategy |
| **`template` is unusable for concurrent experiments** | E2 | Requires terminating all other connections |
| **`dump_restore` is for cross-machine baselines, not per-experiment reset** | E2, S-0.6 | 105× dearer; its value is portability |
| **Fingerprint content, not just counts** | E1 | A row count cannot see an `UPDATE` |

---

## Bounds on this verdict

- **Postgres only.** Sequence non-transactionality and `SET` transactionality are
  both Postgres behaviours. MySQL's `AUTO_INCREMENT` and its DDL-implicit-commit
  rules differ substantially, and none of this transfers.
- **Single connection, no concurrency.** Every cycle ran serially on one
  connection. Concurrent writers would exercise advisory locks and lock-wait
  behaviour that this spike does not touch — and concurrency is a refusal
  category for *fixes*, not for *experiments*, so it will eventually matter.
- **Ten cycles, one dataset, one workload shape.** The workload inserts, updates
  and deletes, which is broader than an insert-only workload would have been, but
  it does not create tables, alter schema, or write outside the database. **A
  workload that writes a file or sends an email is not reset by any strategy
  here** — the S-0.3 finding that repositories need external services is the same
  boundary showing up again.
- **`CONN_MAX_AGE = 0`.** Connections were closed between cycles deliberately, so
  that connection reuse could not mask or manufacture leakage. A production-shaped
  configuration holding connections open is a different test.

---

## Follow-on

- **S-0.6** can now pin its target with a working reset. `rollback+setval` on
  `django-helpdesk` is measured, correct, and 19 ms.
- **S-0.7's fixture repository** should plant *a workload that leaves state
  outside the database* — a written file or a populated in-process cache — since
  that is the case no strategy here resets and the one most likely to be assumed
  away.
- **E2's `reset()`** inherits the four-part verification and the sequence-restore
  step directly from this harness.
- The **cached-queryset result deserves an adversarial test** per the project's
  testing rules: a test that evaluates a queryset, resets, and asserts the stale
  object is *not* trusted.
