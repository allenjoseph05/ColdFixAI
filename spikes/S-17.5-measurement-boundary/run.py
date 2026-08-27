"""S-17.5. Measure the gap between what the subject costs and what reaching it costs.

Run: `uv run python spikes/S-17.5-measurement-boundary/run.py`

Two numbers and one exception, which between them decide what `Binder` can be.

**The endpoint time**, as the subject reports it: `FlaskAdapter.run_workload`
drives the route inside the subject's own interpreter and returns the median of
its own `perf_counter` samples, warm-up discarded. This is what the workload
actually costs.

**The subprocess time**: how long the same call takes measured from *outside* —
interpreter startup, imports, `create_app`, then the request. This is what
`measure_once` would record as `seconds` if a `BoundWorkload.invoke` launched the
subject, because `measure_once` times the callable it is given.

**The collision**: whether the out-of-process path can put its own numbers back
through `extra_counters` instead, or whether `measure_once` reserves the names.

No model calls. Arithmetic and a stopwatch.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession

from coldfix.adapters import Subject
from coldfix.adapters.flask import FlaskAdapter
from coldfix.bench.stats import fit_growth
from coldfix.primitives.measurement import measure_once

HERE = Path(__file__).resolve().parent
SUBJECT = HERE / "subject"
ROUNDS = 15
TICKETS = 20
PER_TICKET = 3


def seed(tickets: int = TICKETS) -> None:
    """Fill the subject's database using its own models."""
    sys.path.insert(0, str(SUBJECT))
    engine = None
    try:
        import shop  # type: ignore[import-not-found]

        database = SUBJECT / "subject.db"
        # Windows will not unlink a file SQLite still holds, and the engine from
        # the previous scale point is still open unless it was disposed. Dispose
        # first, then remove.
        shop.engine.dispose()
        database.unlink(missing_ok=True)
        engine = create_engine(f"sqlite:///{database.as_posix()}")
        shop.Base.metadata.create_all(engine)
        with OrmSession(engine) as session:
            for index in range(tickets):
                ticket = shop.Ticket(title=f"ticket {index}")
                ticket.followups = [shop.Followup() for _ in range(PER_TICKET)]
                session.add(ticket)
            session.commit()
    finally:
        if engine is not None:
            engine.dispose()
        sys.path.remove(str(SUBJECT))
        sys.modules.pop("shop", None)


def measure() -> dict[str, object]:
    adapter = FlaskAdapter(app="shop:create_app")
    subject = Subject(root=SUBJECT, python=[sys.executable])

    endpoint: list[float] = []
    subprocess_wall: list[float] = []
    queries: list[int] = []

    for _ in range(ROUNDS):
        started = time.perf_counter()
        drive = adapter.run_workload(
            subject,
            entry_point="/tickets",
            scale=TICKETS,
            created={"ticket": TICKETS},
            repeats=1,
            timeout=300.0,
        )
        subprocess_wall.append(time.perf_counter() - started)
        endpoint.append(drive.seconds)
        queries.append(drive.queries)

    return {
        "rounds": ROUNDS,
        "endpoint_median_s": statistics.median(endpoint),
        "subprocess_median_s": statistics.median(subprocess_wall),
        "endpoint_samples": endpoint,
        "subprocess_samples": subprocess_wall,
        "queries": queries,
    }


def _supplying(name: str, value: float) -> Callable[[], Mapping[str, float]]:
    """Bound now rather than closed over, so the loop below tests each metric."""
    return lambda: {name: value}


def collision() -> dict[str, object]:
    """Can the out-of-process path hand its own numbers back through `extra_counters`?

    `measure_once` records `seconds` itself, from the callable it timed. If a
    binding tried to replace that with the subject's own figure, this is what
    happens.
    """
    attempted: dict[str, object] = {}
    for name, value in (("seconds", 0.001), ("db.query", 21.0), ("response_bytes", 512.0)):
        try:
            measure_once(lambda: None, extra_counters=_supplying(name, value))
        except Exception as error:  # noqa: BLE001 - the spike is recording which error
            attempted[name] = f"{type(error).__name__}: {error}"
        else:
            attempted[name] = "accepted"
    return attempted


SCALES = (10, 40, 160)


def sweep() -> dict[str, object]:
    """The same workload at three scales, timed both ways, then fitted both ways.

    This is the finding rather than the ratio above: screening fits growth on
    `seconds`, so what matters is not that the outside number is larger but
    whether it still has the *shape* the workload has.
    """
    adapter = FlaskAdapter(app="shop:create_app")
    subject = Subject(root=SUBJECT, python=[sys.executable])

    endpoint: list[float] = []
    outside: list[float] = []
    queries: list[int] = []

    for scale in SCALES:
        seed(tickets=scale)
        inner: list[float] = []
        outer: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            drive = adapter.run_workload(
                subject,
                entry_point="/tickets",
                scale=scale,
                created={"ticket": scale},
                repeats=1,
                timeout=300.0,
            )
            outer.append(time.perf_counter() - started)
            inner.append(drive.seconds)
        endpoint.append(statistics.median(inner))
        outside.append(statistics.median(outer))
        queries.append(drive.queries)

    inside_fit = fit_growth(SCALES, endpoint)
    outside_fit = fit_growth(SCALES, outside)
    query_fit = fit_growth(SCALES, [float(count) for count in queries])
    return {
        "scales": list(SCALES),
        "endpoint_s": endpoint,
        "outside_s": outside,
        "queries": queries,
        "endpoint_growth": inside_fit.growth.name if inside_fit.growth else None,
        "outside_growth": outside_fit.growth.name if outside_fit.growth else None,
        "query_growth": query_fit.growth.name if query_fit.growth else None,
        "endpoint_exponent": inside_fit.exponent,
        "outside_exponent": outside_fit.exponent,
    }


def main() -> None:
    seed()
    timings = measure()
    reserved = collision()

    endpoint = float(timings["endpoint_median_s"])  # type: ignore[arg-type]
    outside = float(timings["subprocess_median_s"])  # type: ignore[arg-type]
    overhead = outside - endpoint

    print(f"rounds: {timings['rounds']}, queries per drive: {set(timings['queries'])}")  # type: ignore[index]
    print(f"endpoint, as the subject measured it : {endpoint * 1000:9.3f} ms")
    print(f"subprocess, measured from outside    : {outside * 1000:9.3f} ms")
    print(f"overhead (startup, imports, app)     : {overhead * 1000:9.3f} ms")
    print(f"ratio outside/endpoint               : {outside / endpoint:9.1f}x")
    print(f"share of the outside number that is the endpoint: {endpoint / outside:.4%}")
    print()
    print("extra_counters, attempted per metric:")
    for name, outcome in reserved.items():
        print(f"  {name:<16} {outcome}")

    print()
    print("the same workload at three scales, fitted both ways:")
    swept = sweep()
    print(f"  scales           : {swept['scales']}")
    print(f"  queries          : {swept['queries']}  -> {swept['query_growth']}")
    inner = [f"{value * 1000:.2f}" for value in swept["endpoint_s"]]  # type: ignore[union-attr]
    outer = [f"{value * 1000:.1f}" for value in swept["outside_s"]]  # type: ignore[union-attr]
    print(f"  endpoint ms      : {inner}  -> {swept['endpoint_growth']}")
    print(f"  outside ms       : {outer}  -> {swept['outside_growth']}")

    (HERE / "measurements.json").write_text(
        json.dumps({"timings": timings, "extra_counters": reserved, "sweep": swept}, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
