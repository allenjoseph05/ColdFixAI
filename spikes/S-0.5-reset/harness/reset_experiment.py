"""S-0.5 — is state reset reliable?

Every experiment this system runs assumes it starts from a known state. If a
reset silently leaves something behind, then measurement #2 is not comparable to
measurement #1 and every ablation delta built on top of them is suspect.

Four strategies are tested over ten cycles each:

| Strategy | Mechanism |
|---|---|
| `rollback` | run the workload inside a transaction, then `ROLLBACK` |
| `rollback+setval` | the same, then restore every sequence explicitly |
| `template` | `DROP DATABASE` + `CREATE DATABASE ... TEMPLATE snapshot` |
| `dump_restore` | `pg_restore --clean` from a custom-format `pg_dump` |

The second exists because the first fails, and the repair turns out to cost
19 ms against a template copy's 163 ms.

Each cycle: fingerprint the database, run a workload that writes, reset,
fingerprint again, compare against the baseline taken before cycle 1.

## What "identical" has to mean

The AC asks that row counts match. Row counts alone are too weak a check, and
the story's own note says why — *"sequence counters, cached querysets, and
connection-level state commonly survive a rollback"*. A database whose row
counts match but whose next primary key is 3,000 higher is not in the same
state; the next experiment will insert rows with different ids, and anything
keyed on or ordered by id behaves differently.

So the fingerprint captures four things per cycle:

- **row count** for every table in the public schema
- **`md5` of the whole table**, ordered by id — because a row count cannot see an
  `UPDATE`, and the workload deliberately renames an existing row
- **`max(id)`** for every table that has one
- **`last_value`** for every sequence

and the leak probes below check the three survivors the note names, one of which
turns out not to survive.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spike_settings")
django.setup()

import psycopg  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from helpdesk.models import FollowUp, Queue, Ticket  # noqa: E402

CYCLES = 10
DB_NAME = "spike_reset"
SNAPSHOT_DB = "spike_reset_snapshot"
DUMP_PATH = "/tmp/spike_reset.dump"
PG_BIN = "/usr/lib/postgresql/16/bin"
DSN_TEMPLATE = "host=postgres user=coldfix_test password=coldfix_test dbname={db}"

# Rows the workload writes per cycle. Enough to move several sequences and
# touch three tables, small enough that the cost of the workload does not
# dominate the cost of the reset being measured.
WORKLOAD_TICKETS = 25
WORKLOAD_FOLLOWUPS_EACH = 2


def _maintenance_connection() -> psycopg.Connection:
    """A connection to the maintenance database, outside the subject.

    `DROP DATABASE` cannot run from a connection to the database being dropped,
    and it cannot run inside a transaction block, hence `autocommit`.
    """
    conn = psycopg.connect(DSN_TEMPLATE.format(db="postgres"))
    conn.autocommit = True
    return conn


def fingerprint() -> dict[str, Any]:
    """Capture the database's observable state.

    Taken over its own short-lived connection rather than Django's, so that
    nothing about Django's transaction or connection state can influence what is
    observed. For the rollback strategy this is essential: reading through the
    same connection that just rolled back would be reading through the object
    under test.
    """
    with psycopg.connect(DSN_TEMPLATE.format(db=DB_NAME)) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables = [r[0] for r in cur.fetchall()]

        counts: dict[str, int] = {}
        max_ids: dict[str, int | None] = {}
        content: dict[str, str | None] = {}
        for table in tables:
            cur.execute(f'SELECT count(*) FROM "{table}"')
            counts[table] = cur.fetchone()[0]

            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s AND column_name='id'",
                (table,),
            )
            if cur.fetchone():
                cur.execute(f'SELECT max(id) FROM "{table}"')
                max_ids[table] = cur.fetchone()[0]

                # Row counts cannot see an UPDATE. The workload deliberately
                # renames an existing row, and a reset that restored the right
                # number of rows with the wrong contents would pass every other
                # check here. Hashing the whole table, ordered by id so the
                # digest does not depend on physical row order, closes that gap.
                cur.execute(
                    f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.id), '')) "
                    f'FROM "{table}" t'
                )
                content[table] = cur.fetchone()[0]

        cur.execute(
            "SELECT sequencename, last_value FROM pg_sequences "
            "WHERE schemaname='public' ORDER BY sequencename"
        )
        sequences = {r[0]: r[1] for r in cur.fetchall()}

    return {
        "counts": counts,
        "max_ids": max_ids,
        "content": content,
        "sequences": sequences,
    }


def diff_fingerprints(base: dict[str, Any], now: dict[str, Any]) -> dict[str, Any]:
    """Everything that differs between two fingerprints, by category."""
    drift: dict[str, Any] = {"counts": {}, "max_ids": {}, "content": {}, "sequences": {}}
    for category, bucket in drift.items():
        for key, base_value in base[category].items():
            now_value = now[category].get(key)
            if base_value != now_value:
                bucket[key] = {"before": base_value, "after": now_value}
    return drift


def workload() -> None:
    """Write to the database — this is what a reset has to undo.

    Creates, updates and deletes, because the three leave different traces. A
    workload that only inserts would let a reset that merely truncates look
    correct.
    """
    queue = Queue.objects.order_by("id").first()
    if queue is None:
        raise SystemExit("no Queue — load the fixture and scale script first")

    made = []
    for i in range(WORKLOAD_TICKETS):
        ticket = Ticket.objects.create(
            title=f"workload-ticket-{i:03d}",
            queue=queue,
            submitter_email=f"workload{i:03d}@example.org",
            status=Ticket.OPEN_STATUS,
            priority=3,
            description="Written by the S-0.5 workload; a reset must remove this.",
        )
        made.append(ticket)
        for f in range(WORKLOAD_FOLLOWUPS_EACH):
            FollowUp.objects.create(
                ticket=ticket, title=f"workload-followup-{i:03d}-{f}", public=True
            )

    # Update an existing row the workload did not create, so a reset that only
    # deletes new rows is caught.
    Ticket.objects.filter(title__startswith="scaled-ticket-000").update(
        title="workload-MUTATED-scaled-ticket-000"
    )

    # Delete some pre-existing rows, so a reset that only removes new rows is
    # also caught.
    doomed = list(
        Ticket.objects.filter(title__startswith="scaled-ticket-01").values_list("id", flat=True)[:5]
    )
    Ticket.objects.filter(id__in=doomed).delete()


def _pg(cmd: list[str]) -> float:
    """Run a Postgres client binary, returning elapsed seconds."""
    env = {**os.environ, "PGPASSWORD": "coldfix_test", "PATH": f"{PG_BIN}:{os.environ['PATH']}"}
    start = time.perf_counter()
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed: {result.stderr[:600]}")
    # A restore that "succeeds with errors ignored" is not a reset. Treat any
    # stderr from pg_restore as failure rather than noise.
    if "error" in result.stderr.lower():
        raise SystemExit(f"{cmd[0]} reported errors: {result.stderr[:600]}")
    return elapsed


def strategy_rollback() -> float:
    """Workload inside a transaction, then roll it back.

    Explicit transaction control rather than `transaction.atomic()`, so that the
    clock covers the `ROLLBACK` alone. The first version of this wrapped the
    whole `atomic()` block and so charged the workload to the rollback strategy
    while the other two strategies ran their workload outside the timer — which
    made rollback look comparable to a template copy when it is in fact an order
    of magnitude cheaper. All three now time only the reset.
    """
    connection.set_autocommit(False)
    try:
        workload()
        start = time.perf_counter()
        connection.rollback()
        elapsed = time.perf_counter() - start
    finally:
        connection.set_autocommit(True)
    connection.close()
    return elapsed


def _baseline_sequence_state() -> list[tuple[str, int, bool]]:
    """Every sequence's restore target: (name, value, is_called).

    `pg_sequences.last_value` is NULL for a sequence that has never been used,
    and a NULL cannot be handed to `setval`. Those are restored to their start
    value with `is_called=false` instead, which is the state a never-called
    sequence is actually in — otherwise the first `nextval` after a reset would
    skip a number.
    """
    state: list[tuple[str, int, bool]] = []
    with psycopg.connect(DSN_TEMPLATE.format(db=DB_NAME)) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sequencename, last_value, start_value FROM pg_sequences "
            "WHERE schemaname='public' ORDER BY sequencename"
        )
        for name, last_value, start_value in cur.fetchall():
            if last_value is None:
                state.append((name, start_value, False))
            else:
                state.append((name, last_value, True))
    return state


def strategy_rollback_with_sequences(
    baseline_sequences: list[tuple[str, int, bool]],
) -> float:
    """Rollback, then explicitly restore every sequence.

    The obvious repair for the defect `rollback` exhibits. Worth measuring
    because rollback is roughly 400x cheaper than a template copy, so if a
    handful of `setval` calls closes the gap it is the strategy the design
    should use.
    """
    connection.set_autocommit(False)
    try:
        workload()
        start = time.perf_counter()
        connection.rollback()
        with connection.cursor() as cur:
            for name, value, is_called in baseline_sequences:
                cur.execute("SELECT setval(%s, %s, %s)", (f'public."{name}"', value, is_called))
        connection.commit()
        elapsed = time.perf_counter() - start
    finally:
        connection.set_autocommit(True)
    connection.close()
    return elapsed


def strategy_template() -> float:
    """Workload committed, then the database replaced from a template copy."""
    workload()
    connection.close()

    start = time.perf_counter()
    with _maintenance_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=%s AND pid <> pg_backend_pid()",
            (DB_NAME,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{DB_NAME}" TEMPLATE "{SNAPSHOT_DB}"')
    return time.perf_counter() - start


def strategy_dump_restore() -> float:
    """Workload committed, then restored from a `pg_dump` archive."""
    workload()
    connection.close()

    return _pg(
        [
            f"{PG_BIN}/pg_restore",
            "-h",
            "postgres",
            "-U",
            "coldfix_test",
            "-d",
            DB_NAME,
            "--clean",
            "--if-exists",
            "--no-owner",
            DUMP_PATH,
        ]
    )


def leak_probes() -> dict[str, Any]:
    """The three survivors the story's note names, checked directly.

    These are not caught by comparing row counts, which is exactly why the note
    calls them out separately.
    """
    probes: dict[str, Any] = {}

    # 1. Sequence counters. Postgres sequences are non-transactional by design:
    #    nextval() takes effect immediately so that concurrent writers never
    #    receive the same id, and ROLLBACK does not give the number back.
    before = fingerprint()["sequences"]
    with transaction.atomic():
        Ticket.objects.create(
            title="probe-ticket",
            queue=Queue.objects.order_by("id").first(),
            submitter_email="probe@example.org",
            status=Ticket.OPEN_STATUS,
            priority=3,
            description="sequence probe",
        )
        transaction.set_rollback(True)
    connection.close()
    after = fingerprint()["sequences"]
    moved = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    probes["sequences_survive_rollback"] = {
        "moved": {k: {"before": v[0], "after": v[1]} for k, v in moved.items()},
        "leaked": bool(moved),
    }

    # 2. Cached querysets. A Django QuerySet caches its rows in the Python
    #    object the first time it is evaluated. A database rollback cannot reach
    #    into that object, so anything holding an evaluated queryset across a
    #    reset keeps serving rows that no longer exist.
    with transaction.atomic():
        Ticket.objects.create(
            title="cache-probe-ticket",
            queue=Queue.objects.order_by("id").first(),
            submitter_email="cacheprobe@example.org",
            status=Ticket.OPEN_STATUS,
            priority=3,
            description="queryset cache probe",
        )
        cached = Ticket.objects.filter(title="cache-probe-ticket")
        rows_seen_before_rollback = len(cached)  # forces evaluation and caches
        transaction.set_rollback(True)
    connection.close()

    stale_rows = len(cached._result_cache or [])
    fresh_rows = Ticket.objects.filter(title="cache-probe-ticket").count()
    probes["queryset_cache_survives_rollback"] = {
        "rows_cached_in_python": stale_rows,
        "rows_actually_in_database": fresh_rows,
        "evaluated_before_rollback": rows_seen_before_rollback,
        "leaked": stale_rows != fresh_rows,
    }

    # 3. Connection-level state. A session-level SET is not transactional in the
    #    way a row write is; SET LOCAL would be. Anything the harness or the
    #    subject sets at session scope outlives the rollback on that connection.
    with connection.cursor() as cur:
        cur.execute("SET application_name = 'coldfix-probe'")
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SET application_name = 'coldfix-probe-inside-txn'")
        transaction.set_rollback(True)
    with connection.cursor() as cur:
        cur.execute("SHOW application_name")
        surviving = cur.fetchone()[0]
    probes["session_state_survives_rollback"] = {
        "set_inside_transaction": "coldfix-probe-inside-txn",
        "value_after_rollback": surviving,
        "leaked": surviving == "coldfix-probe-inside-txn",
    }
    connection.close()

    return probes


def run_strategy(name: str, reset: Any, baseline: dict[str, Any]) -> dict[str, Any]:
    """Ten cycles of one strategy, comparing state to the baseline every cycle."""
    durations: list[float] = []
    drifts: list[dict[str, Any]] = []
    clean_cycles = 0

    for _ in range(CYCLES):
        durations.append(reset())
        drift = diff_fingerprints(baseline, fingerprint())
        drifts.append(drift)
        if not any(drift[c] for c in drift):
            clean_cycles += 1

    counts_clean = all(not d["counts"] for d in drifts)
    content_clean = all(not d["content"] for d in drifts)
    everything_clean = clean_cycles == CYCLES

    return {
        "strategy": name,
        "cycles": CYCLES,
        "clean_cycles": clean_cycles,
        "row_counts_always_identical": counts_clean,
        "content_always_identical": content_clean,
        "fully_identical": everything_clean,
        "median_seconds": sorted(durations)[len(durations) // 2],
        "min_seconds": min(durations),
        "max_seconds": max(durations),
        "final_drift": drifts[-1],
    }


def main() -> None:
    print("preparing snapshots\n")
    connection.close()

    # Snapshot 1: a template database, for the template strategy.
    with _maintenance_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname IN (%s, %s) AND pid <> pg_backend_pid()",
            (DB_NAME, SNAPSHOT_DB),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{SNAPSHOT_DB}"')
        cur.execute(f'CREATE DATABASE "{SNAPSHOT_DB}" TEMPLATE "{DB_NAME}"')

    # Snapshot 2: a dump file, for the dump/restore strategy.
    dump_seconds = _pg(
        [
            f"{PG_BIN}/pg_dump",
            "-h",
            "postgres",
            "-U",
            "coldfix_test",
            "-d",
            DB_NAME,
            "-Fc",
            "-f",
            DUMP_PATH,
        ]
    )
    print(f"pg_dump took {dump_seconds * 1000:.0f} ms -> {DUMP_PATH}")

    baseline_sequences = _baseline_sequence_state()
    baseline = fingerprint()
    print(
        f"baseline: {len(baseline['counts'])} tables, "
        f"{sum(baseline['counts'].values())} rows, "
        f"{len(baseline['sequences'])} sequences\n"
    )

    results = []
    for name, reset in (
        ("rollback", strategy_rollback),
        (
            "rollback+setval",
            lambda: strategy_rollback_with_sequences(baseline_sequences),
        ),
        ("template", strategy_template),
        ("dump_restore", strategy_dump_restore),
    ):
        print(f"running {CYCLES} cycles of {name} ...")
        result = run_strategy(name, reset, baseline)
        results.append(result)
        print(
            f"  clean cycles {result['clean_cycles']}/{CYCLES}  |  "
            f"row counts always identical: {result['row_counts_always_identical']}  |  "
            f"median reset {result['median_seconds'] * 1000:.1f} ms"
        )
        drift = result["final_drift"]
        for category in ("counts", "max_ids", "content", "sequences"):
            if drift[category]:
                sample = dict(list(drift[category].items())[:3])
                print(f"    {category}: {len(drift[category])} differ, e.g. {sample}")
        print()

    print("--- leak probes (the three the story's note names) ---")
    probes = leak_probes()
    for name, probe in probes.items():
        print(f"  {name}: leaked={probe['leaked']}")
        print(f"    {json.dumps({k: v for k, v in probe.items() if k != 'leaked'})[:220]}")

    out = Path("/results/reset.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "cycles": CYCLES,
                "baseline_tables": len(baseline["counts"]),
                "baseline_rows": sum(baseline["counts"].values()),
                "pg_dump_seconds": dump_seconds,
                "strategies": results,
                "leak_probes": probes,
            },
            indent=2,
            default=str,
        )
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
