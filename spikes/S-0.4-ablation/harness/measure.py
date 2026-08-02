"""S-0.4 — does ablation produce clean deltas?

Measures `GET /api/tickets/` under three conditions and reports whether the
ablation delta is separable from measurement noise.

| Condition  | `followup_set` returns          | What it measures |
|------------|---------------------------------|------------------|
| `baseline` | the real thing                  | nothing removed |
| `replay`   | a value recorded from a real    | the component alone |
|            | baseline response               | |
| `empty`    | `[]`                            | the component *plus* all |
|            |                                 | downstream work its output caused |

The last column is the whole point of the second half of the AC. If those two
stub strategies produce materially different numbers, then "we ablated the
serializer and it was worth X" is not a well-formed statement until the strategy
is named — which is what S-3.4 requires be recorded.

## Three methodological choices, and why

**In-process test client, not HTTP against a live server.** The AC requires the
conditions be *interleaved*, and interleaving exists to cancel drift — cache
warming, CPU frequency scaling, competing containers — that a
20-then-20 block design would silently absorb into the delta. Interleaving
request-by-request means toggling the stub between consecutive requests, which
means the toggle must live in the process that serves them. Driving an external
server would force a restart per condition, i.e. block design, i.e. the exact
thing interleaving is for. Socket and HTTP-parsing overhead would also add
variance to every condition without being part of what is being compared.

**The patch is installed once and switched by a flag**, rather than applied and
removed around each request. Patching per request would charge `setattr` cost to
the treatment conditions only, which is a real difference between conditions
that has nothing to do with serialization. With a flag, all three conditions run
identical machinery and differ only where intended. The baseline pays one global
read per field, and that is the correct place for the cost to land.

**Timing includes response rendering.** DRF responses are lazy, so stopping the
clock at `client.get()` would exclude JSON serialization of the payload — and
payload size is precisely where `replay` and `empty` diverge. Excluding it would
hide the effect the third condition exists to expose.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Running `python /harness/measure.py` puts /harness on sys.path but not the
# working directory, so the subject's own packages (`demodesk`, and `helpdesk`
# via the editable install) would not import. `manage.py` gets this for free by
# living in the tree; this harness deliberately does not live in the tree.
sys.path.insert(0, str(Path.cwd()))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spike_settings")
django.setup()

from django.db import connection, reset_queries  # noqa: E402
from django.test import Client  # noqa: E402
from rest_framework import serializers  # noqa: E402
from stats import Summary, compare, summarize  # noqa: E402

ENDPOINT = "/api/tickets/?page_size=100"
TARGET_FIELD = "followup_set"
HTTP_OK = 200
REPS = 20
WARMUP = 5
CONDITIONS = ("baseline", "replay", "empty")

# The stub currently in force. `None` means "call the real implementation".
_active: str | None = None
_replay_value: list[dict[str, Any]] = []

_original_to_representation = serializers.ListSerializer.to_representation


def _patched_to_representation(self: Any, data: Any) -> Any:
    """Ablate `followup_set` only.

    Guarding on `field_name` keeps the ablation narrow. `ListSerializer` backs
    every `many=True` field in the project, so patching it unconditionally would
    also stub `followupattachment_set` and anything else, and the measured delta
    would no longer belong to a nameable component.
    """
    if self.field_name == TARGET_FIELD:
        if _active == "replay":
            return _replay_value
        if _active == "empty":
            return []
    return _original_to_representation(self, data)


serializers.ListSerializer.to_representation = _patched_to_representation


def _login() -> Client:
    from django.contrib.auth.models import User

    client = Client()
    user = User.objects.filter(is_superuser=True).first()
    if user is None:
        raise SystemExit("no superuser — load demodesk/fixtures/demo.json first")
    client.force_login(user)
    return client


def _record_replay_value(client: Client) -> list[dict[str, Any]]:
    """Capture a *representative* real `followup_set` from an unstubbed response.

    S-3.4 calls this "records a real return value for the target during a
    baseline run". One ticket's followups are recorded and replayed for every
    ticket, because a replay keyed per instance would need a store the size of
    the response — which is what the fallback clause in S-3.4 exists to avoid.

    **Representative, not merely real.** The first draft of this took the first
    ticket with any followups at all and got one with a single followup, because
    the demo fixture's three original tickets sort ahead of the 500 synthesized
    ones. Replaying a 1-element list where the true median is 6 makes the replay
    payload nearly as small as the empty stub's, and the two strategies then look
    interchangeable — the precise conclusion this spike exists to test, arrived
    at through a sampling error rather than through evidence.

    So the recorded value is the one whose length is closest to the median across
    the page. A replay stub that is not size-representative is not measuring the
    component alone; it is measuring the component plus an unstated fraction of
    the downstream work, which is the empty stub's semantics wearing a disguise.
    """
    global _active
    _active = None
    payload = json.loads(client.get(ENDPOINT).content)
    candidates = [list(row.get(TARGET_FIELD) or []) for row in payload["results"]]
    non_empty = [c for c in candidates if c]
    if not non_empty:
        raise SystemExit("no ticket in the first page has followups — nothing to record")

    lengths = sorted(len(c) for c in non_empty)
    target_length = lengths[len(lengths) // 2]
    best = min(non_empty, key=lambda c: abs(len(c) - target_length))
    print(
        f"recorded replay value: {len(best)} followups "
        f"(page median {target_length}, range {lengths[0]}-{lengths[-1]})"
    )
    return best


def _timed_request(client: Client) -> tuple[float, int]:
    """One measured request. Returns (seconds, response bytes)."""
    start = time.perf_counter()
    response = client.get(ENDPOINT)
    body = response.content
    elapsed = time.perf_counter() - start
    if response.status_code != HTTP_OK:
        raise SystemExit(f"endpoint returned {response.status_code}, not 200")
    return elapsed, len(body)


def _guard_counters(client: Client) -> dict[str, dict[str, int]]:
    """Query count and payload size per condition.

    Taken in a separate untimed pass. Query counting needs a debug cursor, which
    records the SQL text of every statement — measurable work, and instrumenting
    the thing under measurement would corrupt the timings this spike exists to
    produce. Per ADR 008 this uses `force_debug_cursor`, never `settings.DEBUG`.

    These are the guard counters the project requires on every metric: a stub
    that made the endpoint faster while returning *more* rows, or while issuing
    the same number of queries, would not be an ablation of anything.
    """
    global _active
    counters: dict[str, dict[str, int]] = {}
    connection.force_debug_cursor = True
    try:
        for condition in CONDITIONS:
            _active = None if condition == "baseline" else condition
            reset_queries()
            response = client.get(ENDPOINT)
            body = response.content
            payload = json.loads(body)
            # Group by the leading fragment of each statement so the residual
            # after ablation can be named rather than guessed at. An ablation
            # that removes one N+1 and leaves a second one standing is a
            # perfectly good result, but only if you can see the second one.
            signatures: dict[str, int] = {}
            for q in connection.queries:
                table = q["sql"][:60].split('"')[1] if '"' in q["sql"] else q["sql"][:40]
                signatures[table] = signatures.get(table, 0) + 1

            counters[condition] = {
                "queries": len(connection.queries),
                "bytes": len(body),
                "tickets_returned": len(payload["results"]),
                "followups_in_payload": sum(
                    len(row.get(TARGET_FIELD, [])) for row in payload["results"]
                ),
                "by_table": dict(sorted(signatures.items(), key=lambda kv: -kv[1])[:5]),
            }
    finally:
        connection.force_debug_cursor = False
        _active = None
    return counters


def main() -> None:
    global _active, _replay_value

    client = _login()
    _replay_value = _record_replay_value(client)

    counters = _guard_counters(client)

    _active = None
    for _ in range(WARMUP):
        _timed_request(client)

    samples: dict[str, list[float]] = {c: [] for c in CONDITIONS}
    sizes: dict[str, list[int]] = {c: [] for c in CONDITIONS}

    for rep in range(REPS):
        # Rotate the within-rep order so no condition sits permanently in the
        # position that follows the others' cache effects.
        shift = rep % len(CONDITIONS)
        for condition in CONDITIONS[shift:] + CONDITIONS[:shift]:
            _active = None if condition == "baseline" else condition
            elapsed, size = _timed_request(client)
            samples[condition].append(elapsed)
            sizes[condition].append(size)
    _active = None

    summaries: dict[str, Summary] = {c: summarize(samples[c]) for c in CONDITIONS}

    print(f"\nendpoint      {ENDPOINT}")
    print(f"target field  {TARGET_FIELD}")
    print(
        f"design        {REPS} reps x {len(CONDITIONS)} conditions, interleaved, "
        f"{WARMUP} warm-up requests discarded"
    )
    print(f"replay value  {len(_replay_value)} followups recorded from a real response")

    print("\n--- guard counters (separate untimed pass) ---")
    for condition in CONDITIONS:
        c = counters[condition]
        print(
            f"{condition:<10} queries={c['queries']:<6} bytes={c['bytes']:<9} "
            f"tickets={c['tickets_returned']:<5} followups_in_payload={c['followups_in_payload']}"
        )
        print(f"{'':<10} by table: {c['by_table']}")

    print("\n--- timings ---")
    for condition in CONDITIONS:
        print(summaries[condition].line(condition))

    print("\n--- ablation deltas vs baseline ---")
    comparisons: dict[str, Any] = {}
    for condition in ("replay", "empty"):
        cmp = compare(samples["baseline"], samples[condition])
        comparisons[condition] = cmp
        verdict = "SEPARABLE" if cmp.separable else "not separable"
        print(
            f"baseline -> {condition:<8} "
            f"median {summaries['baseline'].median * 1000:.2f} -> "
            f"{summaries[condition].median * 1000:.2f} ms "
            f"({cmp.median_ratio:.3f}x, {cmp.median_shift * 1000:+.2f} ms)  "
            f"p={cmp.p_value:.2e}  delta={cmp.cliffs_delta:+.3f} ({cmp.effect_label})  "
            f"{verdict}"
        )

    print("\n--- do the two stub strategies differ from each other? ---")
    strategy_cmp = compare(samples["replay"], samples["empty"])
    verdict = "MATERIALLY DIFFERENT" if strategy_cmp.separable else "indistinguishable"
    print(
        f"replay -> empty    median {summaries['replay'].median * 1000:.2f} -> "
        f"{summaries['empty'].median * 1000:.2f} ms "
        f"({strategy_cmp.median_ratio:.3f}x, {strategy_cmp.median_shift * 1000:+.2f} ms)  "
        f"p={strategy_cmp.p_value:.2e}  "
        f"delta={strategy_cmp.cliffs_delta:+.3f} ({strategy_cmp.effect_label})  {verdict}"
    )

    out = Path("/results/ablation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "endpoint": ENDPOINT,
                "target_field": TARGET_FIELD,
                "reps": REPS,
                "warmup_discarded": WARMUP,
                "replay_followups_recorded": len(_replay_value),
                "guard_counters": counters,
                "timings_seconds": samples,
                "response_bytes": sizes,
                "summaries": {
                    c: {
                        "n": s.n,
                        "mean": s.mean,
                        "median": s.median,
                        "stdev": s.stdev,
                        "cv": s.cv,
                        "min": s.minimum,
                        "max": s.maximum,
                    }
                    for c, s in summaries.items()
                },
                "comparisons": {
                    name: {
                        "u": c.u,
                        "z": c.z,
                        "p_value": c.p_value,
                        "cliffs_delta": c.cliffs_delta,
                        "effect": c.effect_label,
                        "median_shift": c.median_shift,
                        "median_ratio": c.median_ratio,
                        "separable": c.separable,
                    }
                    for name, c in (
                        *comparisons.items(),
                        ("replay_vs_empty", strategy_cmp),
                    )
                },
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
