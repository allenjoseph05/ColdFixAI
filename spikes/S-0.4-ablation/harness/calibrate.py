"""S-0.4, second half — how small a delta can this method actually detect?

`measure.py` establishes that a 3.3x ablation delta separates cleanly. That is
a weak claim on its own: an effect that large would separate under almost any
method, and the story's stated worry is the opposite case — *"if its
measurements are noisy the design's core is unsound"*. The number that matters
for the design is the **minimum detectable difference**: below which size does
an ablation delta stop being distinguishable from noise, at the repetition count
we can afford?

Method: take the ablated endpoint as a base, inject a *known* extra delay into
the same patch point, and sweep the delay downward until separability fails.
Because the injected delay is known exactly, any disagreement between "what was
injected" and "what was measured" is the instrument's error, not a property of
the subject.

This is a calibration, in the metrology sense. It is the difference between
"the scale moved" and "this scale reads accurately down to 2 grams".

## The injector has its own floor — do not read the bottom rows as measurement

`time.sleep` costs roughly 80-100 microseconds per call in syscall overhead
regardless of the duration requested; `time.sleep(0)` is not free. The injection
point fires 100 times per request, so **every injected condition carries about
8-10 ms of overhead that has nothing to do with the delay being injected**,
measured directly and reproducibly.

The consequence is that requests for 1, 2, and 5 ms of total injected delay all
produce roughly the same ~20 ms measured shift, because what is being measured
at that end of the sweep is the injector, not the injection. The sweep therefore
bounds the floor from above but cannot resolve below it. The `false-positive
rate` section is the part that bounds it from below, and it does so without
`time.sleep` being involved at all — which is why both halves are needed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spike_settings")
django.setup()

from django.test import Client  # noqa: E402
from rest_framework import serializers  # noqa: E402
from stats import compare, summarize  # noqa: E402

ENDPOINT = "/api/tickets/?page_size=100"
TARGET_FIELD = "followup_set"
HTTP_OK = 200
REPS = 20
WARMUP = 5

# Descending, so the report reads from "obviously detectable" down to the floor.
# The injection point fires once per ticket, so these are ~100x smaller at the
# response level: 0.5 ms/call is a ~50 ms shift, 0.01 ms/call a ~1 ms shift.
INJECTED_DELAYS_MS = (1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01, 0.0)

_inject_seconds = 0.0
_calls = 0
_original_to_representation = serializers.ListSerializer.to_representation


def _patched(self: Any, data: Any) -> Any:
    """Ablate `followup_set`, then optionally burn a known amount of wall time.

    The base condition is the empty stub rather than the untouched endpoint, for
    run time: each request costs ~435 ms instead of ~1455 ms, and the sweep needs
    40 requests per delay. The coefficient of variation of the two conditions was
    within half a percentage point of each other in `measure.py`, so the floor
    derived here transfers.
    """
    global _calls
    if self.field_name == TARGET_FIELD:
        _calls += 1
        if _inject_seconds:
            time.sleep(_inject_seconds)
        return []
    return _original_to_representation(self, data)


serializers.ListSerializer.to_representation = _patched


def _login() -> Client:
    from django.contrib.auth.models import User

    client = Client()
    user = User.objects.filter(is_superuser=True).first()
    if user is None:
        raise SystemExit("no superuser — load demodesk/fixtures/demo.json first")
    client.force_login(user)
    return client


def _timed(client: Client) -> float:
    start = time.perf_counter()
    response = client.get(ENDPOINT)
    # DRF responses render lazily. Forcing the body inside the timed region is
    # the point, not an accident: JSON serialization is real work the endpoint
    # does, and leaving it outside the clock would measure a request that never
    # happens in production.
    _ = response.content
    elapsed = time.perf_counter() - start
    if response.status_code != HTTP_OK:
        raise SystemExit(f"endpoint returned {response.status_code}")
    return elapsed


def main() -> None:
    global _inject_seconds

    client = _login()
    for _ in range(WARMUP):
        _inject_seconds = 0.0
        _timed(client)

    # The injection point runs once per ticket on the page, so a per-call delay
    # is multiplied at the response level. Count the calls exactly rather than
    # inferring the multiplier from a timing probe — the first version of this
    # estimated 121 from five noisy samples when the true figure is 100, which
    # inflated every "expected" figure in the sweep by a fifth. Deriving a
    # constant from a measurement when it can simply be counted is the mistake
    # the project's "counting is code" rule exists to prevent.
    global _calls
    _inject_seconds = 0.0
    _calls = 0
    _timed(client)
    calls_per_request = float(_calls)
    print(f"injection point fires exactly {_calls}x per request (one per ticket)\n")

    results: list[dict[str, Any]] = []
    smallest_separable_ms: float | None = None

    print(
        f"{'injected/call':>13} {'effective':>10} {'base med':>10} {'test med':>10} "
        f"{'measured':>10} {'error':>8} {'CV base':>8} {'CV test':>8} "
        f"{'p':>10} {'delta':>7}  verdict"
    )

    for delay_ms in INJECTED_DELAYS_MS:
        base: list[float] = []
        test: list[float] = []
        for rep in range(REPS):
            # Alternate which condition goes first, so neither systematically
            # inherits the other's cache state.
            order = (0.0, delay_ms) if rep % 2 == 0 else (delay_ms, 0.0)
            for d in order:
                _inject_seconds = d / 1000.0
                elapsed = _timed(client)
                (test if d else base).append(elapsed)
        _inject_seconds = 0.0

        if delay_ms == 0.0:
            # Null control: the same condition against itself. Anything that
            # reads "separable" here is a false positive, and the method's
            # error rate is not something to take on faith.
            half = len(base) // 2
            first, second = base[:half], base[half:]
            cmp = compare(first, second)
            s_base, s_test = summarize(first), summarize(second)
            effective = 0.0
        else:
            cmp = compare(base, test)
            s_base, s_test = summarize(base), summarize(test)
            effective = delay_ms * calls_per_request

        measured_ms = cmp.median_shift * 1000
        error = measured_ms - effective
        verdict = "SEPARABLE" if cmp.separable else "not separable"
        if cmp.separable and delay_ms > 0:
            # The *measured* shift, not the requested one. `time.sleep` under-
            # and over-delivers at these scales, so what was asked for is not
            # ground truth; what the endpoint actually did is. The error column
            # reports the gap so the reader can see how far apart they drift.
            smallest_separable_ms = measured_ms

        print(
            f"{delay_ms:>11.3f}ms {effective:>8.1f}ms "
            f"{s_base.median * 1000:>9.1f}ms {s_test.median * 1000:>9.1f}ms "
            f"{measured_ms:>+9.1f}ms {error:>+7.1f}ms "
            f"{s_base.cv * 100:>7.2f}% {s_test.cv * 100:>7.2f}% "
            f"{cmp.p_value:>10.2e} {cmp.cliffs_delta:>+7.3f}  {verdict}"
        )

        results.append(
            {
                "injected_ms_per_call": delay_ms,
                "effective_ms_per_request": effective,
                "base_median_ms": s_base.median * 1000,
                "test_median_ms": s_test.median * 1000,
                "measured_shift_ms": measured_ms,
                "error_ms": error,
                "cv_base": s_base.cv,
                "cv_test": s_test.cv,
                "p_value": cmp.p_value,
                "cliffs_delta": cmp.cliffs_delta,
                "separable": cmp.separable,
            }
        )

    # --- false-positive rate ------------------------------------------------
    #
    # The sweep above produced a result that cannot be true: a 2 ms injection
    # reading as a 22 ms separable shift. Nothing was done differently between
    # those two conditions, so whatever separated them was drift, not signal.
    #
    # One anomaly is an anecdote. This measures the rate directly: run the
    # identical condition against itself, repeatedly, and count how often the
    # test claims a difference. A calibrated test at p<0.01 should fire on
    # roughly 1 run in 100. Anything materially above that means the assumption
    # of independent samples is being violated by drift, and the honest
    # conclusion is that separability at this repetition count cannot stand on
    # its own.
    print("\n--- false-positive rate: identical condition against itself ---")
    null_trials = 12
    false_positives = 0
    null_shifts: list[float] = []
    _inject_seconds = 0.0
    for trial in range(null_trials):
        left: list[float] = []
        right: list[float] = []
        for rep in range(REPS):
            first, second = (left, right) if rep % 2 == 0 else (right, left)
            first.append(_timed(client))
            second.append(_timed(client))
        cmp = compare(left, right)
        null_shifts.append(cmp.median_shift * 1000)
        flagged = cmp.separable
        false_positives += int(flagged)
        print(
            f"  null trial {trial + 1:>2}  shift={cmp.median_shift * 1000:>+7.2f}ms  "
            f"p={cmp.p_value:>9.2e}  delta={cmp.cliffs_delta:>+6.3f}  "
            f"{'FALSE POSITIVE' if flagged else 'ok'}"
        )

    worst_null = max(abs(s) for s in null_shifts)
    print(
        f"\nfalse positives: {false_positives}/{null_trials} "
        f"({false_positives / null_trials * 100:.0f}%)  |  "
        f"largest spurious shift observed: {worst_null:.2f} ms"
    )

    base_median_ms = results[0]["base_median_ms"]
    print()
    if smallest_separable_ms is not None:
        print(
            f"smallest separable effect (measured): {smallest_separable_ms:.2f} ms "
            f"({smallest_separable_ms / base_median_ms * 100:.2f}% of a "
            f"{base_median_ms:.0f} ms baseline) at {REPS} reps per condition"
        )
    else:
        print("nothing in the sweep separated — the floor is above the largest delay")

    out = Path("/results/calibration.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "endpoint": ENDPOINT,
                "reps": REPS,
                "calls_per_request": calls_per_request,
                "smallest_separable_ms": smallest_separable_ms,
                "sweep": results,
                "null_trials": null_trials,
                "false_positives": false_positives,
                "null_shifts_ms": null_shifts,
                "largest_spurious_shift_ms": worst_null,
            },
            indent=2,
        )
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
